"""Explicit coverage registry: missing integrations must never masquerade as prices."""
REGISTRY = {
    'Steam Community Market': {'supported': True, 'docs': 'https://steamcommunity.com/market/'},
    'Market.CSGO': {'supported': True, 'docs': 'https://market.csgo.com/docs/'},
    'Skinport': {'supported': True, 'docs': 'https://docs.skinport.com/'},
    'CSFloat': {'supported': True, 'docs': 'https://docs.csfloat.com/', 'key': 'CSFLOAT_API_KEY'},
    'DMarket': {'supported': False, 'docs': 'https://docs.dmarket.com/', 'reason': 'Публичный API закрыт (HTTP 410). Новый API требует авторизацию; формат цен пока не проверен.'},
    'BitSkins': {'supported': False, 'docs': 'https://docs.bitskins.com/'},
    'SkinBaron': {'supported': True, 'docs': 'https://skinbaron.de/'},
    'GamerPay': {'supported': False, 'docs': 'https://gamerpay.gg/'},
    'BUFF163': {'supported': False, 'docs': 'https://buff.163.com/'},
    'BUFF Market': {'supported': False, 'docs': 'https://buff.market/'},
    'YouPin': {'supported': False, 'docs': 'https://www.youpin898.com/'},
    'C5Game': {'supported': False, 'docs': 'https://www.c5game.com/'},
    'LIS-SKINS': {'supported': True, 'docs': 'https://lis-skins.com/market/csgo/danger-zone-case/'},
    'Waxpeer': {'supported': True, 'docs': 'https://waxpeer.com/'},
    'ShadowPay': {'supported': False, 'docs': 'https://shadowpay.com/'},
    'white.market': {'supported': False, 'docs': 'https://white.market/'},
    'Tradeit': {'supported': True, 'docs': 'https://tradeit.gg/'},
}

# Free public comparison rows provide a fallback for blocked/closed direct APIs.
from public_pages import COMPARISON_MARKETS
for slug, name in COMPARISON_MARKETS.items():
    if name not in REGISTRY:
        REGISTRY[name] = {'docs': 'https://csgoskins.gg/markets/' + slug}
    REGISTRY[name]['supported'] = True
    REGISTRY[name]['comparison'] = True
    REGISTRY[name].pop('reason', None)
