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
    assert response.json()['extra']['system1models.ai']['classifier']=='local conservative prior'
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
