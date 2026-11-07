# hiyori-subs

Stáhne **české a slovenské titulky k anime** z [Hiyori](https://hiyori.cz) přímo k tvým videím.

Projde složku s anime, sám pozná, o které anime a díl jde, a vedle videa uloží titulky jako `<video>.cs.ass` nebo `<video>.sk.srt`. Takové titulky si sám najde Jellyfin, Plex, Kodi, mpv i VLC.

Používá jen oficiální **[Hiyori API v2](https://api.hiyori.cz/scalar/v2/)**. Web Hiyori ani weby fansubů nescrapuje.

## Co umí

- **Párování anime** podle názvu složky nebo souboru přes vyhledávání v Hiyori. Rozumí:
  - knihovně ve stylu Sonarru/Jellyfinu: `Název (2023)/Season 01/Název - S01E05 - ….mkv`;
  - releasům od fansubů: `[SubsPlease] Název S2 - 05 (1080p).mkv`, klidně i v jedné složce s více anime;
  - tagům v názvu složky: `[hiyori-21389]` nebo `[mal-52991]`;
  - filmům: složka `Název (2016)` s jedním videem.
- Co se nespáruje samo, vypíše i s možnými kandidáty. Spáruješ to jednou přes `--id` a skript si to zapamatuje.
- **Výběr titulků:** jazyk podle pořadí v `languages`, shoda release group se souborem videa (SubsPlease, Erai-raws…), nejvyšší verze.
- **Aktualizace:** když na Hiyori přibude novější verze titulků nebo titulky v preferovanějším jazyce, nahradí je.
- **Nepřepisuje** titulky, které jsi k videu dal sám.
- **Bazarr:** po stažení mu řekne, ať titulky načte z disku (viz [Napojení na Bazarr](#napojení-na-bazarr)).
- **Šetrnost k API:** seznamy titulků cachuje (24 h) a obnoví je dřív, jen když se anime objeví mezi nejnovějšími titulky. Opakovaný běh nad stejnou knihovnou tak pošle 1–2 požadavky.

Stahují se jen titulky **uložené přímo na Hiyori**. U titulků, na které Hiyori jen odkazuje (weby fansubů), vypíše `--links` odkaz, kde je najdeš.

## Požadavky

- Python 3.8+ a `requests` (`pip install requests` nebo `sudo apt install python3-requests`)
- **účet na [hiyori.cz](https://hiyori.cz) s povoleným přístupem k API.** O přístup napiš adminům Hiyori. Skript potřebuje tyto scopes (jen čtení):

  | scope | k čemu |
  |---|---|
  | `anime.read` | vyhledání anime podle názvu nebo MAL ID |
  | `subtitles.read` | seznam titulků k anime, nejnovější titulky |
  | `subtitles.download` | stažení souborů titulků |

## Instalace

```bash
git clone https://github.com/ancestor93/hiyori-subs.git ~/hiyori-subs
python3 ~/hiyori-subs/hiyori_subs.py --login
```

`--login` se zeptá na jméno a heslo k Hiyori, získá **API klíč** (platí 3 měsíce) a uloží ho do `~/.config/hiyori-subs/config.json` s právy `600`.

- Heslo se standardně **neukládá**. Klíč pak po 3 měsících obnovíš znovu přes `--login` (skript na to 14 dní předem upozorní).
- Když na otázku „Uložit i jméno a heslo“ odpovíš `a`, skript si klíč obnoví sám. Hodí se to pro běh přes systemd.
- Místo uložení hesla můžeš jméno a heslo předat v proměnných `HIYORI_USERNAME` a `HIYORI_PASSWORD`.
- API klíč z webu Hiyori můžeš vložit ručně do `config.json` jako `"api_key"`. Platnost si skript zjistí sám.

Konfigurační soubor nikdy nedávej do gitu ani nikam nesdílej.

## Použití

```bash
python3 hiyori_subs.py -n ~/Videa/Anime          # nanečisto: jen vypíše, co by stáhl
python3 hiyori_subs.py ~/Videa/Anime             # stáhne chybějící titulky
python3 hiyori_subs.py "$HOME/Videa/Anime/Frieren/Season 01/Frieren - S01E05.mkv"   # jeden soubor
python3 hiyori_subs.py --links ~/Videa/Anime     # vypíše i odkazy na titulky na webech fansubů
python3 hiyori_subs.py -l SK,CZ ~/Videa/Anime    # slovenské titulky mají přednost
```

### Ruční párování

Když skript anime nespáruje, vypíše třeba:

```
NESPÁROVÁNO: Tougen Anki – řada 2 (/media/anime/Tougen Anki (2025)/Season 02)
   možná: --id 22386  Tougen Anki (2025, TV Série, 24 ep.)
   možná: --id 23354  Tougen Anki: Nikko Kegon no Taki-hen (2026, TV Série, 12 ep.)
```

Spáruj ho a zároveň stáhni titulky:

```bash
python3 hiyori_subs.py --id 23354 "/media/anime/Tougen Anki (2025)/Season 02"
```

Hiyori ID najdeš také v adrese anime na webu (`hiyori.cz/anime/<id>/…`).

Když se díly ve složce číslují jinak než na Hiyori, použij `--offset`: číslo dílu na Hiyori = číslo v souboru − offset.

```bash
# 2. řada má v souborech díly 13–24, na Hiyori je to samostatné anime s díly 1–12
python3 hiyori_subs.py --id 12345 --offset 12 "/media/anime/Název/Season 02"
# Hiyori má jedno anime se 148 díly, knihovna ho dělí do řad; S02E01 = díl 59
python3 hiyori_subs.py --id 8550 --offset -58 "/media/anime/Hunter x Hunter (2011)/Season 02"
```

Špatné automatické párování zrušíš přes `--forget` (skript pak složku spáruje znovu).

### Pravidelné spouštění (systemd, uživatelská služba)

```bash
mkdir -p ~/.config/systemd/user
cp ~/hiyori-subs/systemd/hiyori-subs.{service,timer} ~/.config/systemd/user/
# v hiyori-subs.service uprav cestu ke knihovně anime
systemctl --user daemon-reload
systemctl --user enable --now hiyori-subs.timer
journalctl --user -u hiyori-subs
```

Na serveru bez přihlášeného uživatele zapni `loginctl enable-linger <uživatel>`, nebo službu dej do `/etc/systemd/system/` s `User=`.

## Napojení na Bazarr

Hiyori v Bazarru není jako poskytovatel titulků a Bazarr neumí přidat vlastního. hiyori-subs proto běží vedle Bazarru: stáhne titulky k videím a Bazarr je převezme jako externí titulky. Epizoda se pak v Bazarru ukáže jako „má CZ titulky“ a Bazarr je už nehledá u svých poskytovatelů.

Bazarr načítá titulky z disku sám jen jednou denně (ve 4:00). Když v configu vyplníš sekci `bazarr`, hiyori-subs mu po každém stažení řekne, ať je načte hned. Vyzkoušeno s Bazarrem 1.6.

**1. hiyori-subs musí vidět stejné soubory jako Bazarr.** Spouštěj ho na stejném stroji nebo v kontejneru se stejnou knihovnou. Cesty se shodovat nemusí (třeba `/media/anime` na hostiteli a `/tv` v Dockeru). Titulky se ukládají vedle videa, takže je Bazarr najde.

**2. Zapni v Bazarru češtinu (a slovenštinu):**
1. *Settings → Languages → Languages Filter*: přidej **Czech**, případně **Slovak**.
2. *Settings → Languages → Languages Profiles*: vytvoř profil (např. „Anime CZ“) s Czech, případně Slovak, a ulož.
3. Profil přiřaď anime: *Series* (a *Movies*) → u anime ikona úprav → *Languages Profile*. Pro hromadnou změnu použij v seznamu *Mass Edit*.

**3. Zkopíruj API klíč Bazarru:** *Settings → General → Security → API Key*.

**4. Doplň ho do `~/.config/hiyori-subs/config.json`:**

```json
{
 "api_key": "…klíč Hiyori, vyplní --login…",
 "bazarr": {
  "url": "http://localhost:6767",
  "apikey": "KLÍČ_Z_BAZARRU"
 }
}
```

`url` je adresa, na které Bazarr běží: stejný stroj `http://localhost:6767`, Docker síť `http://bazarr:6767`, jinak IP serveru. Když máš Bazarr za reverzní proxy s podcestou, přidej ji (`http://server/bazarr`).

**5. Spouštěj hiyori-subs pravidelně** přes [systemd](#pravidelné-spouštění-systemd-uživatelská-služba) nebo cron. Po běhu, ve kterém se něco stáhlo, uvidíš ve výpisu:

```
Bazarr: spuštěno series_full_scan_subtitles
Bazarr: spuštěno movies_full_scan_subtitles
```

V Bazarru to najdeš v *System → Tasks* (*Index All Existing Episodes Subtitles*) a u epizody se objeví štítek **cs**.

**Ruční test spojení** (Bazarr odpoví `204`):

```bash
curl -i -X POST -H "X-API-KEY: KLÍČ_Z_BAZARRU" -d taskid=series_full_scan_subtitles http://localhost:6767/api/system/tasks
```

Dobré vědět:
- Když Bazarr stáhne CZ titulky dřív sám (z jiného poskytovatele), hiyori-subs je nepřepíše, protože k videu už titulky existují. Když chceš, aby měly přednost titulky z Hiyori, spouštěj hiyori-subs častěji, než Bazarr hledá chybějící titulky (*Settings → Scheduler → Search for Missing Series Subtitles*).
- Titulky z Hiyori Bazarr nebere jako vlastní, takže je neuvidí v *History* a nebude je „vylepšovat“ (*Upgrade Previously Downloaded Subtitles*). Novější verzi z Hiyori stáhne sám hiyori-subs.
- Bez sekce `bazarr` v configu funguje vše stejně, jen Bazarr titulky uvidí až po noční indexaci.

## Nastavení

`~/.config/hiyori-subs/config.json` (jiné umístění: `--config` nebo proměnná `HIYORI_SUBS_CONFIG`), vzor je v [`examples/config.example.json`](examples/config.example.json):

| klíč | výchozí | význam |
|---|---|---|
| `api_key`, `api_key_expires` | – | API klíč Hiyori (vyplní `--login`) |
| `username`, `password` | – | nepovinné, jen pro automatickou obnovu klíče |
| `languages` | `["CZ", "SK"]` | jazyky titulků v pořadí priority |
| `request_delay` | `2.0` | pauza mezi požadavky na API v sekundách (limit Hiyori je 30 za minutu) |
| `rows_ttl_h` | `24` | jak dlouho platí cache seznamu titulků jednoho anime |
| `bazarr.url`, `bazarr.apikey` | – | nepovinné: adresa a API klíč Bazarru, po stažení titulků v něm spustí načtení z disku |
| `bazarr.tasks` | obě úlohy | které úlohy Bazarru spustit (`series_full_scan_subtitles`, `movies_full_scan_subtitles`) |

Stav (párování, cache API, co bylo staženo) je v `~/.local/state/hiyori-subs/state.json` (`--state` nebo `HIYORI_SUBS_STATE`).

## Poděkování

- [Hiyori](https://hiyori.cz) za API a českou anime komunitu
- všem fansubům, kteří titulky překládají

## Licence

[MIT](LICENSE)
