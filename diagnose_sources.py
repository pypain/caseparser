"""Read-only public source diagnostics; never prints API keys or private responses."""
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from sources import REGISTRY
from collect import SOURCES, fetch_price, error_message, SameHostRedirect


def check(pair):
    name,meta=pair
    if name in SOURCES:
        try:
            price,currency=fetch_price(name)
            return {'market':name,'adapter':True,'status':'ok','price':price,'currency':currency}
        except Exception as e:
            return {'market':name,'adapter':True,'status':'error','error':error_message(e)}
    try:
        req=urllib.request.Request(meta['docs'],headers={'User-Agent':'Caseparser/0.3'})
        with urllib.request.build_opener(SameHostRedirect()).open(req,timeout=12) as r:
            return {'market':name,'adapter':False,'status':'not_connected','website_http_status':r.status}
    except Exception as e:
        return {'market':name,'adapter':False,'status':'not_connected','website_error':error_message(e)}


if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=4) as pool:
        report=list(pool.map(check,REGISTRY.items()))
    print(json.dumps(report,ensure_ascii=False,indent=2))
