# Joblistings

Lokaler Stellen- und Bewerbungstracker. Benötigt Docker mit laufender Engine.

## Starten

```sh
git clone https://github.com/blsknr/joblistings.git
cd joblistings
./joblistings start
```

Das baut das Image beim ersten Start und führt den Container im Vordergrund aus. Öffnen: **http://localhost:8765**. **Ctrl+C** stoppt die App, entfernt den Container und fährt die eigene Colima-VM herunter, sofern dort keine weiteren Container laufen. Kein Autostart.

## Terminalbefehle

```sh
./joblistings start --detach  # Optional im Hintergrund
./joblistings stop            # App stoppen
./joblistings stop --runtime  # App + eigene Colima-VM stoppen
./joblistings status          # Status, startet nichts
./joblistings logs            # Logs verfolgen
./joblistings build           # Image nach Quellcodeänderungen neu bauen
```

Alle Daten bleiben in `./data/`, auch nach dem Stoppen. Den Ordner bei gestoppter App sichern. Das persönliche Suchprofil lässt sich unter **Suchprofil** bearbeiten und liegt in `data/profile.json`. Es wird nicht veröffentlicht.

Anderer Port: `JOBLISTINGS_PORT=8877 ./joblistings start`.

## macOS ohne Docker-Laufzeit

```sh
brew install colima docker
./joblistings start
```

Das Startskript legt bei Bedarf eine eigene Colima-VM `joblistings` an. Mit `./joblistings stop --runtime` wird sie vollständig beendet. Andere Container-Laufzeiten werden nicht gestoppt.

## Direkt mit Docker

Bei Colima vorab in diesem Terminal:

```sh
colima start --profile joblistings --cpus 2 --memory 2 --disk 15 --activate=false
export DOCKER_CONTEXT=colima-joblistings
```

```sh
docker build -t joblistings:local .
mkdir -p data
docker run --rm --init --name joblistings \
  --label io.github.blsknr.joblistings=true \
  --publish 127.0.0.1:8765:8765 \
  --mount "type=bind,source=$PWD/data,target=/app/data" \
  --user "$(id -u):$(id -g)" \
  --read-only --tmpfs /tmp:rw,nosuid,size=256m --shm-size=256m \
  --cpus=2 --memory=1g --pids-limit=256 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --stop-timeout=20 joblistings:local
```

**Ctrl+C** oder in einem zweiten Terminal `docker stop joblistings`. Anschließend beendet `colima stop --profile joblistings` auch die eigene Colima-VM. Der Port ist ausschließlich auf dem eigenen Rechner erreichbar.
