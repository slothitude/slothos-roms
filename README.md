# SlothOS ROMs (Phase 3.0)

LAN-only EmulatorJS backend running on Lappy (`192.168.0.33:8444`).

## Run

```bash
# one-time install
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# one-time EmulatorJS bundle fetch (needs sudo for /opt)
sudo bash scripts/fetch-emujs.sh

# initial ROM scan
curl -X POST http://localhost:8444/api/scan

# serve
.venv/bin/python3 -m uvicorn server:app --host 0.0.0.0 --port 8444
```

systemd unit: `scripts/slothos-roms.service` — install with:
```bash
sudo cp scripts/slothos-roms.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now slothos-roms
```

## Layout

- `/mnt/seagate/ROMs/{gba,nes,snes,psx,arcade}/` — ROM files
- `/var/lib/slothos-roms/catalog.db` — SQLite catalog
- `/var/lib/slothos-roms/saves/{device_id}/{rom_id}.state` — save states
- `/var/cache/slothos-roms/boxart/{system}/{title}.png` — cached box art
- `/opt/slothos-roms/emujs/data/` — EmulatorJS bundle (loader.js, cores/*.wasm)

## Endpoints

See plan / `server.py`.

## Netplay (Phase 3.0)

Built into EmulatorJS:
1. Player 1 opens `/play/{id}`, clicks Netplay → Host, gets room code.
2. Players 2+ open same URL, click Netplay → Join, enter code.
3. WebRTC peer-to-peer over LAN; Lappy only serves bytes.
