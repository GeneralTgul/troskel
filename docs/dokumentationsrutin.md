# Dokumentationsrutin

*Hur dokumentationen i det här repot skrivs, struktureras och kontrolleras. Läs den innan du lägger till eller flyttar en sida.*

<!-- toc -->
## Innehåll

- [Grundreglerna](#grundreglerna)
- [Strukturen](#strukturen)
  - [Äldre sidor](#äldre-sidor)
- [Språket](#språket)
- [Innehållsförteckningen](#innehållsförteckningen)
- [Indexet](#indexet)
- [Skärmdumparna](#skärmdumparna)
- [Pensionerade ord](#pensionerade-ord)
- [Kontrollerna vid commit](#kontrollerna-vid-commit)
<!-- /toc -->

## Grundreglerna

- **Texten beskriver nuläget.** När något ändras skrivs stycket om så att det stämmer i dag. Gammal text står inte kvar bredvid den nya, och historiken finns i `git log`.
- **Skriv bara det som finns i koden.** Det som inte är provat på riktig hårdvara märks ut och länkar till [PROV.md](PROV.md).
- **Ett ämne står på ett ställe.** Andra sidor länkar dit i stället för att upprepa det.
- **Verktygen skriver det som går att räkna fram:** innehållsförteckningar, indexet och skärmdumparna. Det ändras aldrig för hand.

## Strukturen

```
README.md                      ingången på engelska
README.sv.md                   kort ingång på svenska, pekar på docs/index.md
docs/
  index.md                     ingången, skrivs av tools/docs_index.py
  dokumentationsrutin.md       den här sidan
  <kapitel>/
    <kapitel>.md               kapitlets översikt
    images/                    bara den här sidans bilder
    <sida>/
      <sida>.md                en undersida
      images/                  bara undersidans bilder
tools/
  docs_index.yaml              indexets grupper, sidor och meningar
  docs_shots.json              vilken vy som blir vilken bild på vilken sida
  docs_retired.yaml            ord som inte gäller längre
```

- **En sida ligger i en egen mapp med samma namn:** `docs/<kapitel>/<kapitel>.md`, och en undersida som `docs/<kapitel>/<sida>/<sida>.md`. Det gäller även en sida utan bilder, eftersom den kan få bilder senare.
- **Namnen är kebab-case:** små bokstäver, siffror och bindestreck (`under-testet`). Å, ä och ö är tillåtna.
- **Bilderna ligger i sidans egen `images/`** och länkas relativt: `![Sidan Idrottare](images/idrottare.png)`. En sida visar aldrig en annan sidas bild.
- **Skript ligger i `tools/`,** aldrig under `docs/`.
- **Kapitel eller undersida:** ett ämne som går att läsa för sig får ett eget kapitel. Det som bara fördjupar ett kapitel, och skulle göra det för långt, blir en undersida. Kapitelsidan är översikten, och undersidan länkar tillbaka med `../<kapitel>.md`.

### Äldre sidor

Sidorna som fanns före rutinen ligger kvar på sina adresser, eftersom ärenden och andra repon länkar dit: `ARKITEKTUR.md`, `DATASKYDD.md`, `PROV.md`, `TRANINGSPLAN.md` och de fyra sidorna i `sporter/`. De står i listan `LEGACY` i [`tools/docs_check.py`](../tools/docs_check.py) och följer alla regler utom mappregeln. En ny sida läggs aldrig i den listan. Behöver en äldre sida bilder flyttas den först till en egen mapp, och länkarna dit rättas.

## Språket

- **Dokumentationen är på svenska.** Koden, kommentarerna och verktygens utskrifter är på engelska. Undantaget är [`README.md`](../README.md), ingången på engelska för den som hittar repot utifrån. Den svenska ingången är [`README.sv.md`](../README.sv.md). Den engelska säger samma sak som den svenska i korthet, med mer om programmet och ändras när programmets sporter, utrustning, bygget, dataskyddet eller licensen ändras.
- **Knappar, sidor och fält heter som i programmet** och skrivs kursivt: *Nytt test*, *Starta livevyn*, *Fyll i din klocka*. Texterna finns i `src/lopanalys/i18n/*.sv.json`.
- **Korta meningar och hela ord.** En förkortning skrivs ut första gången den används på sidan: laktattröskelpuls (LTHR).
- **Den som läser handboken är coachen,** inte utvecklaren. Kommandon och filnamn hör hemma i [ARKITEKTUR.md](ARKITEKTUR.md) och på den här sidan.
- **Inga riktiga personer** i exempel och bilder, bara demoidrottarna. Hälsodata, inspelningar och rapporter läggs aldrig i repot.

## Innehållsförteckningen

En sida får en innehållsförteckning när den har de två markörerna `<!-- toc -->` och `<!-- /toc -->` på var sin rad. [`tools/docs_toc.py`](../tools/docs_toc.py) skriver sidans `##`- och `###`-rubriker mellan dem. Rubriker som ska synas där måste alltså vara riktiga rubriker, inte fetstil.

**Undersidorna listas av sig själva.** En sida med undersidor får *Undersidor* sist i innehållsförteckningen, med varje undersidas rubrik som länk, även om sidan saknar markörerna. Skriv därför ingen egen lista över undersidor. En ny, omdöpt eller borttagen undersida syns vid nästa commit.

## Indexet

[`docs/index.md`](index.md) skrivs av [`tools/docs_index.py`](../tools/docs_index.py) ur [`tools/docs_index.yaml`](../tools/docs_index.yaml).

- **En ny sida får en rad i yaml-filen** när den inte är en undersida: gruppen, sökvägen under `docs/` och en mening om vad man hittar där (högst 100 tecken). Utan raden stoppas committen.
- **Undersidor behöver ingen rad.** De läggs in indragna under sin sida.
- **Titeln är sidans första rubrik** fram till första tankstrecket eller kolonet, och sidorna står i bokstavsordning inom gruppen. Grupperna står i yaml-filens ordning.

## Skärmdumparna

Bilderna av programmet tas av [`tools/docs_shots.py`](../tools/docs_shots.py). Det startar programmet i demoläget utan skärm, går till varje vy och sparar en PNG i sidans `images/`.

```bash
source scripts/headless_env.sh        # Linux utan skärm
python tools/docs_shots.py            # stillbilderna, och rörliga bilder som saknas
python tools/docs_shots.py --all      # alla bilder
python tools/docs_shots.py tv         # bara den vyn
python tools/docs_shots.py --list     # vyerna som verktyget kan ta
```

På en delad maskin körs det med ett eget minnestak: `systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 python tools/docs_shots.py`.

- **Listan är [`tools/docs_shots.json`](../tools/docs_shots.json):** en rad per bild med vyn, sidan och filnamnet. En ny bild av en vy som verktyget redan kan är en ny rad där. En ny vy är en ny metod i klassen `Studio` och ett namn i `VIEWS`.
- **Bara demodata.** Bilderna visar demoidrottarna och simulerade enheter. Verktyget använder en egen tom datamapp som tas bort efteråt, och hämtar inget från internet.
- **Bilderna ändras inte i onödan.** Fönstret har en fast storlek (1440 × 900, TV-vyn 1920 × 1080), demotesterna får fasta datum, och en fil skrivs bara om när den nya bilden skiljer sig mer än brus från den gamla. Åldern på demoidrottarna räknas från dagens datum, så de bilderna ändras när någon av dem fyller år.
- **Rörliga vyer har `"live": true`:** kamerabilden och ett pågående test ser olika ut varje gång. De tas bara när filen saknas, när vyn nämns vid namn eller med `--all`. Ta om dem när livevyn eller operatörssidan har ändrats.
- **Egna bilder** (foton av utrustningen, en utskrift) läggs för hand i sidans `images/`. Titta på bilden innan den committas, så att den inte visar en riktig person eller riktiga mätvärden.

Skärmdumpen av fliken *Rapport* tas inte, eftersom den inbyggda webbvyn inte går att fotografera utan skärm.

## Pensionerade ord

När en knapp, en sida, en fil eller en inställning byter namn läggs det gamla namnet i [`tools/docs_retired.yaml`](../tools/docs_retired.yaml) med det som gäller nu. Kontrollen varnar när det gamla ordet står kvar i dokumentationen, utom på en rad som säger *tidigare*, *hette*, *förut* eller *ersatt*.

## Kontrollerna vid commit

Kroken installeras en gång per klon:

```bash
git config core.hooksPath tools/hooks
```

Den körs när något under `docs/`, `README.md`, `README.sv.md` eller verktygens filer committas, och gör tre saker:

1. skriver innehållsförteckningen i sidorna som committas,
2. skriver `docs/index.md`,
3. kör [`tools/docs_check.py`](../tools/docs_check.py) över alla sidor.

| Stoppar committen | Varnar |
|---|---|
| Länk till en fil eller ett ankare (`#rubrik`) som inte finns | Pensionerat ord utan *tidigare* eller *hette* på raden |
| Bild som saknas, ligger utanför sidans egen `images/` eller inte visas av någon sida | Gränssnittet eller de svenska texterna ändrades: ta om skärmdumparna |
| Sida som inte ligger som `<namn>/<namn>.md` i kebab-case | |
| Sida utan rad i `tools/docs_index.yaml`, eller rad utan sida | |
| Innehållsförteckning som inte stämmer med rubrikerna eller undersidorna på en sida som inte är med i committen | |
| Rad i `tools/docs_shots.json` med en okänd vy eller en sida som inte finns | |

Kör kontrollen för hand efter en flytt: `python tools/docs_check.py`. Samma kontroll körs av testet `tests/tools/test_docs_tools.py`, så felen syns också i testsviten.

**Flytta en sida:** flytta hela mappen med `git mv`, rätta raden i `tools/docs_index.yaml` och `tools/docs_shots.json`, och kör `python tools/docs_check.py`. Den listar varje länk som pekar på den gamla adressen.
