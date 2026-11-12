"""A globe on the phone to fill the place list of the "Luogo" television in the Home app.

Served by the Mac while `scenes.py casa` runs: open http://<mac-name>.local:8765 on the phone.
Pick one of the six places, then turn the globe and tap a point (or search a name): the place is renamed on
the strip, chosen, and shown. The Home app shows the same six names as the inputs of "Luogo".
Coordinates are remembered by name in tools/local.json ("coords").
"""
import json
import threading
from urllib.parse import quote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from demo_effects import lan_get, read_local, update_local

PORT = 8765
MAX_BODY = 2048
NAME_BYTES = 24   # PLACE_NAME_LEN in the firmware
SLOTS = 6         # PLACE_SLOTS in the firmware


def strip_name(name: str) -> str:
    """The name exactly as the strip will keep it (cleanPlaceName in src/main.cpp): printable, no quotes or
    backslashes, at most NAME_BYTES of UTF-8 cut on a whole character, no trailing spaces."""
    kept = "".join(char for char in name if char.isprintable() and char not in '"\\')
    encoded = kept.encode()[:NAME_BYTES]
    return encoded.decode(errors="ignore").rstrip(" ")


def clean_place(data: dict) -> dict:
    """The only shape accepted from the page: a short name, real coordinates, a slot."""
    latitude, longitude = float(data["lat"]), float(data["lon"])
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("coordinate fuori dal mondo")
    slot = int(data.get("slot", 1))
    if not 1 <= slot <= SLOTS:
        raise ValueError("posto: da 1 a 6")
    name = strip_name(str(data.get("name", ""))) or strip_name(f"{latitude:.2f}, {longitude:.2f}")
    return {"name": name, "lat": round(latitude, 3), "lon": round(longitude, 3), "slot": slot}


def remember(place: dict) -> None:
    coords = read_local().get("coords")
    coords = coords if isinstance(coords, dict) else {}
    update_local({"coords": {**coords, place["name"]: [place["lat"], place["lon"]]}})


def make_handler(strip: str) -> type[BaseHTTPRequestHandler]:
    class Globe(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/":
                return self.reply(200, PAGE, "text/html; charset=utf-8")
            if self.path == "/api/place":
                state = lan_get(strip, "/api/state")
                places = lan_get(strip, "/api/places")
                is_on = "luogo" in json.loads(state).get("scenes", "").split(",") if state else None
                listed = json.loads(places) if places else {"slot": 1, "names": []}
                coords = read_local().get("coords") or {}
                return self.reply(200, json.dumps({**listed, "on": is_on, "coords": coords}), "application/json")
            self.reply(404, "not found", "text/plain")

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self.reply(413, "too big", "text/plain")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/place":
                    place = clean_place(body)
                    remember(place)
                    if lan_get(strip, f"/api/places?slot={place['slot']}&chosen=1&name={quote(place['name'])}") is None:
                        return self.reply(502, "la striscia non risponde (o ha un firmware senza posti)", "text/plain")
                    is_shown = lan_get(strip, "/api/scene?name=luogo&on=1") is not None if body.get("show") else False
                    return self.reply(200, json.dumps({"place": place, "on": is_shown}), "application/json")
                if self.path == "/api/off":
                    lan_get(strip, "/api/scene?name=luogo&on=0")
                    return self.reply(200, json.dumps({"on": False}), "application/json")
            except (KeyError, TypeError, ValueError) as error:
                return self.reply(400, str(error), "text/plain")
            self.reply(404, "not found", "text/plain")

        def reply(self, code: int, text: str, kind: str) -> None:
            body = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            pass

    return Globe


def serve(strip: str, port: int | None = None, say: Callable[[str], None] = print) -> ThreadingHTTPServer | None:
    """Start the page in the background; None when the port is taken (another helper already serves it)."""
    port = port or PORT
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(strip))
    except OSError as error:
        say(f"globo non avviato sulla porta {port}: {error}")
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


PAGE = r"""<!doctype html>
<html lang="it"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scegli il luogo</title>
<style>
:root{--bg:#f4f1ea;--ink:#1d2430;--muted:#5d6675;--sea:#cfdced;--land:#8fa58a;--line:#ffffff88;--night:#0b1530;--accent:#d2552d;--card:#fffdf8}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--ink:#e8e6e1;--muted:#9aa3b2;--sea:#1b2a44;--land:#4f6b52;--line:#ffffff22;--night:#000000;--accent:#ff8a5c;--card:#161b24}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.4 system-ui,-apple-system,sans-serif}
main{max-width:560px;margin:0 auto;padding:16px}h1{font-size:20px;margin:4px 0 12px}
canvas{display:block;margin:0 auto;touch-action:none;cursor:grab;max-width:100%}
.row{display:flex;gap:8px;margin-top:12px}input{flex:1;min-width:0;padding:12px;border-radius:10px;border:1px solid var(--muted);background:var(--card);color:var(--ink);font-size:16px}
button{padding:12px 14px;border-radius:10px;background:var(--card);color:var(--ink);font-size:16px;border:1px solid var(--muted)}
button.go{background:var(--accent);color:#fff;border-color:var(--accent);flex:1}button:disabled{opacity:.4}
ul{list-style:none;padding:0;margin:8px 0}li{padding:10px 12px;border-radius:10px;background:var(--card);margin-bottom:6px}
.slots{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:12px}
.slots button{font-size:14px;padding:10px 6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.slots button.on{border:2px solid var(--accent);font-weight:600}
#pick{margin-top:12px;font-weight:600}#status{color:var(--muted);margin-top:8px;min-height:1.4em}
</style></head><body><main>
<h1>Dove guarda lo specchio?</h1>
<canvas id="g"></canvas>
<div id="slots" class="slots"></div>
<div id="pick">Scegli quale dei 6 posti cambiare, poi tocca un punto sul globo o cerca un nome.</div>
<div class="row"><input id="q" placeholder="Città, isola, montagna" enterkeyhint="search"><button id="find">Cerca</button></div>
<ul id="results"></ul>
<div class="row"><button class="go" id="show" disabled>Mostra sullo specchio</button><button id="off">Spegni</button></div>
<div id="status"></div>
</main>
<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js"></script>
<script>
const $=id=>document.getElementById(id), css=n=>getComputedStyle(document.body).getPropertyValue(n).trim();
const size=Math.min(innerWidth-32,520), ratio=devicePixelRatio||1, canvas=$('g'), ctx=canvas.getContext('2d');
canvas.width=canvas.height=size*ratio; canvas.style.width=canvas.style.height=size+'px'; ctx.scale(ratio,ratio);
const projection=d3.geoOrthographic().scale(size/2-4).translate([size/2,size/2]).clipAngle(90).rotate([-12,-42]);
const path=d3.geoPath(projection,ctx), rad=Math.PI/180;
let land=null, chosen=null, slot=1, names=[], coords={};
function showSlots(){
  $('show').textContent='Metti al posto '+slot+' e mostra';$('slots').innerHTML='';
  names.forEach((n,i)=>{const b=document.createElement('button');b.textContent=(i+1)+'. '+n;b.className=i+1===slot?'on':'';
    b.onclick=()=>{slot=i+1;showSlots();const c=coords[n];if(c)choose({name:n,lat:c[0],lon:c[1]},true);};$('slots').appendChild(b);});
}

function subsolar(ms){  // where the sun is overhead now (same formula as tools/nature.py)
  const d=(ms-946728000000)/86400000, g=(357.528+0.9856003*d)*rad, L=(280.460+0.9856474*d)*rad;
  const lam=L+(1.915*Math.sin(g)+0.020*Math.sin(2*g))*rad, eps=(23.439-0.0000004*d)*rad;
  const dec=Math.asin(Math.sin(eps)*Math.sin(lam)), ra=Math.atan2(Math.cos(eps)*Math.sin(lam),Math.cos(lam));
  const gmst=(18.697374558+24.06570982441908*d)%24;
  return [((ra/rad-gmst*15)%360+540)%360-180, dec/rad];
}
function draw(){
  ctx.clearRect(0,0,size,size);
  ctx.beginPath();path({type:'Sphere'});ctx.fillStyle=css('--sea');ctx.fill();
  ctx.beginPath();path(d3.geoGraticule10());ctx.strokeStyle=css('--line');ctx.lineWidth=.5;ctx.stroke();
  if(land){ctx.beginPath();path(land);ctx.fillStyle=css('--land');ctx.fill();}
  const sun=subsolar(Date.now());
  ctx.beginPath();path(d3.geoCircle().center([sun[0]+180,-sun[1]]).radius(90)());
  ctx.globalAlpha=.42;ctx.fillStyle=css('--night');ctx.fill();ctx.globalAlpha=1;
  if(chosen){const r=projection.rotate();
    if(d3.geoDistance([chosen.lon,chosen.lat],[-r[0],-r[1]])<Math.PI/2){const [x,y]=projection([chosen.lon,chosen.lat]);
      ctx.beginPath();ctx.arc(x,y,7,0,2*Math.PI);ctx.fillStyle=css('--accent');ctx.fill();ctx.lineWidth=2;ctx.strokeStyle='#fff';ctx.stroke();}}
}
function choose(place,turn){
  chosen=place;$('pick').textContent='Scelto: '+place.name;$('show').disabled=false;
  if(turn){const from=projection.rotate(),to=[-place.lon,-place.lat],i=d3.interpolate(from,to),began=Date.now();
    const step=setInterval(()=>{const t=Math.min(1,(Date.now()-began)/900);projection.rotate(i(d3.easeCubic(t)));draw();if(t>=1)clearInterval(step);},16);}
  draw();
}
let start=null,moved=0;
canvas.addEventListener('pointerdown',e=>{start=[e.offsetX,e.offsetY,projection.rotate()];moved=0;canvas.setPointerCapture(e.pointerId);});
canvas.addEventListener('pointermove',e=>{if(!start)return;const dx=e.offsetX-start[0],dy=e.offsetY-start[1];moved=Math.max(moved,Math.hypot(dx,dy));
  const k=90/(size/2);projection.rotate([start[2][0]+dx*k,Math.max(-90,Math.min(90,start[2][1]-dy*k))]);draw();});
canvas.addEventListener('pointerup',e=>{const s=start;start=null;if(!s||moved>6)return;
  const at=projection.invert([e.offsetX,e.offsetY]);if(!at||isNaN(at[0]))return;
  choose({name:at[1].toFixed(2)+', '+at[0].toFixed(2),lat:at[1],lon:at[0]},false);});
async function search(){
  const q=$('q').value.trim();if(!q)return;$('results').innerHTML='';
  try{const r=await fetch('https://geocoding-api.open-meteo.com/v1/search?count=5&language=it&format=json&name='+encodeURIComponent(q));
    const found=(await r.json()).results||[];if(!found.length){$('status').textContent='Nessun posto con questo nome.';return;}
    for(const p of found){const li=document.createElement('li');li.textContent=[p.name,p.admin1,p.country].filter(Boolean).join(', ');
      li.onclick=()=>{$('results').innerHTML='';choose({name:[p.name,p.country].filter(Boolean).join(', '),lat:p.latitude,lon:p.longitude},true);};
      $('results').appendChild(li);}
  }catch(e){$('status').textContent='Ricerca non riuscita: '+e.message;}
}
async function send(url,body){
  try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok)throw new Error(await r.text());return await r.json();}catch(e){$('status').textContent='Non riuscito: '+e.message;return null;}
}
$('find').onclick=search;$('q').addEventListener('keydown',e=>{if(e.key==='Enter')search();});
$('show').onclick=async()=>{if(!chosen)return;const r=await send('/api/place',{...chosen,slot,show:true});if(!r)return;
  names[slot-1]=r.place.name;coords[r.place.name]=[r.place.lat,r.place.lon];showSlots();
  $('status').textContent=(r.on?'Lo specchio mostra il cielo di '+r.place.name+'.':'Salvato.')+' In Casa è il posto '+slot+' di "Luogo".';};
$('off').onclick=async()=>{const r=await send('/api/off',{});if(r)$('status').textContent='Scena spenta.';};
fetch('/api/place').then(r=>r.json()).then(s=>{names=s.names||[];slot=s.slot||1;coords=s.coords||{};showSlots();
  const n=names[slot-1],c=coords[n];if(c)choose({name:n,lat:c[0],lon:c[1]},true);
  $('status').textContent=s.on===null?'La striscia non risponde.':!names.length?'La striscia non ha ancora l\'elenco dei posti (firmware vecchio).':s.on?'Lo specchio sta mostrando '+n+'.':'';}).catch(()=>{});
fetch('https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-110m.json').then(r=>r.json())
  .then(w=>{land=topojson.feature(w,w.objects.land);draw();}).catch(()=>{$('status').textContent='Mappa non caricata: controlla la connessione.';});
draw();setInterval(draw,60000);
</script></body></html>
"""
