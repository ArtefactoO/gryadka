import base64,hashlib,json,secrets,time
from urllib.parse import urlencode
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import RedirectResponse
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

app=FastAPI()
key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));jwk.update(kid='fixture',use='sig',alg='RS256')
codes={}
issuer='http://authmock:9000'

@app.get('/.well-known/openid-configuration')
def discovery():
 return {'issuer':issuer,'authorization_endpoint':issuer+'/authorize','token_endpoint':issuer+'/token','jwks_uri':issuer+'/jwks','response_types_supported':['code'],'subject_types_supported':['public'],'id_token_signing_alg_values_supported':['RS256'],'token_endpoint_auth_methods_supported':['client_secret_basic'],'code_challenge_methods_supported':['S256']}

@app.get('/jwks')
def jwks():return {'keys':[jwk]}

@app.get('/authorize')
def authorize(request:Request):
 q=dict(request.query_params)
 if q.get('redirect_uri')!='http://appoidc:8082/api/auth/oidc/callback' or q.get('code_challenge_method')!='S256':raise HTTPException(400,'Invalid fixture authorization request')
 code=secrets.token_urlsafe(16);codes[code]=q
 return RedirectResponse(q['redirect_uri']+'?'+urlencode({'code':code,'state':q['state']}))

@app.post('/token')
async def token(request:Request):
 form=await request.form();q=codes.pop(form.get('code'),None)
 if q is None:raise HTTPException(400,'Invalid fixture code')
 challenge=base64.urlsafe_b64encode(hashlib.sha256(str(form.get('code_verifier')).encode()).digest()).rstrip(b'=').decode()
 if challenge!=q['code_challenge']:raise HTTPException(400,'Invalid fixture verifier')
 if request.headers.get('authorization')!='Basic '+base64.b64encode(b'fixture-client:fixture-client-secret').decode():raise HTTPException(401,'Invalid fixture client')
 now=int(time.time());claims={'iss':issuer,'aud':'fixture-client','sub':'fixture-user','name':'Fixture Operator','groups':['gryadka-operators'],'iat':now,'exp':now+300,'nonce':q['nonce']}
 return {'access_token':'fixture-access-token','token_type':'Bearer','expires_in':300,'id_token':jwt.encode(claims,key,algorithm='RS256',headers={'kid':'fixture'})}
