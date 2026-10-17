def request_body():
    return {'request':{'model':'ignored','stream':True,'messages':[{'role':'user','content':'Hello'}]},
            'routing':{'candidates':[{'id':'seller-a','model':'vendor/tiny','pricing':{'input':1,'cache_read':0,'output':2}}]}}


def test_local_ranking_without_classifier_or_execution(client,monkeypatch):
    from app import classify, providers
    from app.engine import ENGINE
    tiny=next(m for m in ENGINE.config.raw['models'] if m['name']=='tiny-free')
    monkeypatch.setitem(tiny,'irp_model_id','vendor/tiny')
    async def fail(*args,**kwargs):raise AssertionError('IRP must not call an inference service')
    monkeypatch.setattr(classify,'classify',fail);monkeypatch.setattr(providers,'complete',fail)
    models=client.get('/v1/routing/models').json()
    assert models['object']=='list' and all('candidates' not in m for m in models['data'])
    assert 'vendor/tiny' in {m['id'] for m in models['data']}
    response=client.post('/v1/routing/rank',json=request_body())
    assert response.status_code==200
    assert response.json()['ranked'][0]['candidate_id']=='seller-a'
    extra=response.json()['extra']['system1models.ai']
    assert extra['classifier'].startswith('s1-llm-auto-router@') and extra['category']=='coding'
    assert response.json()['extra']['system1models.ai']['router_fee_usd']==0


def test_unknown_models_and_invalid_json(client):
    b=request_body();b['routing']['candidates'][0]['model']='unknown'
    r=client.post('/v1/routing/rank',json=b)
    assert r.status_code==422 and r.headers['content-type']=='application/problem+json'
    assert client.post('/v1/routing/rank',content='{').status_code==400


def test_catalog_starting_and_limits(client,monkeypatch):
    from app import irp
    from app.engine import ENGINE
    monkeypatch.setattr(ENGINE,'ready',lambda:False)
    r=client.get('/v1/routing/models');assert r.status_code==503 and 'retry-after' in r.headers
    monkeypatch.setattr(ENGINE,'ready',lambda:True)
    monkeypatch.setattr(irp.LIMITER,'per_ip_per_hour',0)
    r=client.post('/v1/routing/rank',json=request_body())
    assert r.status_code==503 and r.json()['type']=='urn:irp:problem:unavailable'


def wide_body():
    b=request_body();b['routing']['candidates'][0]['model']='irptest/model-1'
    return b


def test_classifier_failure_falls_back_to_the_prior_and_says_so(client,monkeypatch):
    from app import classify
    from auto_router import jev
    class Broken:
        loaded=True
        def __call__(self,*a):return jev.FALLBACK
    monkeypatch.setattr(classify,'S1',Broken())
    r=client.post('/v1/routing/rank',json=wide_body())
    assert r.status_code==200
    extra=r.json()['extra']['system1models.ai']
    assert extra['classifier']=='local conservative prior (fallback: error)' and 'category' not in extra


def test_classifier_timeout_and_busy_pool_still_rank(client,monkeypatch):
    import threading
    from app import classify, irp
    release=threading.Event()
    class Slow:
        loaded=True
        def __call__(self,*a):
            release.wait(5)
    monkeypatch.setattr(classify,'S1',Slow())
    monkeypatch.setattr(irp.CLASSIFIER,'timeout_s',0.05)
    try:
        r=client.post('/v1/routing/rank',json=wide_body())
        assert r.status_code==200
        assert r.json()['extra']['system1models.ai']['classifier']=='local conservative prior (fallback: timeout)'
        assert r.json()['ranked'][0]['candidate_id']=='seller-a'
    finally:
        release.set()


def test_models_answer_reports_catalog_and_classifier(client):
    extra=client.get('/v1/routing/models').json()['extra']['system1models.ai']
    assert extra['catalog_generated_at']=='2026-10-17T00:00:00Z'
    assert extra['catalog_models']>=12 and extra['classifier'].startswith('s1-llm-auto-router@')
    ids={m['id'] for m in client.get('/v1/routing/models').json()['data']}
    assert {'irptest/model-3','e2ee-irptest-model-3','tiny-alias'} <= ids


def test_wide_catalog_models_are_ranked_with_caller_prices(client):
    b=request_body()
    b['routing']['candidates']=[
        {'id':'cheap','model':'e2ee-irptest-model-1','pricing':{'input':0.5,'cache_read':0.05,'output':1}},
        {'id':'strong','model':'irptest/model-10','pricing':{'input':9,'cache_read':1,'output':30}},
        {'id':'alias','model':'tiny-alias','pricing':{'input':1,'cache_read':0,'output':2}}]
    b['request']['messages'][0]['content']='A hard coding problem'
    r=client.post('/v1/routing/rank',json=b)
    assert r.status_code==200
    ranked={e['candidate_id']:e for e in r.json()['ranked']}
    assert set(ranked)=={'cheap','strong','alias'}
    assert ranked['strong']['expected_quality']>ranked['cheap']['expected_quality']
    # prices come only from the caller: cost is exactly input/output tokens times the caller's prices
    u=ranked['cheap']['expected_usage']
    assert abs(ranked['cheap']['expected_cost_usd']-(u['input_tokens']*0.5+u['output_tokens']*1)/1e6)<1e-12


def rank_status(client,headers):
    return client.post('/v1/routing/rank',json=wide_body(),headers=headers).status_code


def test_trusted_ip_has_its_own_larger_bucket(client,monkeypatch):
    from app import irp
    monkeypatch.setattr(irp,'TRUSTED',irp.Trusted('65.109.49.103',per_hour=5,per_day=50))
    monkeypatch.setattr(irp.LIMITER,'per_ip_per_hour',1)
    monkeypatch.setattr(irp.LIMITER,'per_subnet_per_hour',1)
    mine={'x-forwarded-for':'65.109.49.103'}
    assert [rank_status(client,mine) for _ in range(5)]==[200]*5
    assert rank_status(client,mine)==503  # the trusted bucket is finite too
    other={'x-forwarded-for':'8.8.8.8'}
    assert rank_status(client,other)==200 and rank_status(client,other)==503  # everyone else keeps the small limit


def test_spoofed_forwarded_for_cannot_claim_the_trusted_bucket(client,monkeypatch):
    from app import irp
    monkeypatch.setattr(irp,'TRUSTED',irp.Trusted('65.109.49.103',per_hour=1000,per_day=1000))
    monkeypatch.setattr(irp.LIMITER,'per_ip_per_hour',1)
    monkeypatch.setattr(irp.LIMITER,'per_subnet_per_hour',1)
    # The caller controls everything left of the proxy-appended address; only the last entry counts.
    spoof={'x-forwarded-for':'65.109.49.103, 203.0.113.9'}
    assert rank_status(client,spoof)==200 and rank_status(client,spoof)==503
    # no forwarded header at all: the socket peer ('testclient') is not trusted either
    assert rank_status(client,{})==200 and rank_status(client,{})==503
    assert not irp.TRUSTED.contains('not an ip') and irp.TRUSTED.contains('::ffff:65.109.49.103')


def test_malformed_trusted_list_never_widens_access():
    from app import irp
    t=irp.Trusted('garbage, ,999.1.1.1')
    assert t.networks==[] and not t.contains('65.109.49.103')
