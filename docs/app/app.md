# Appen för idrottaren

*Schemat som coachen gör i Tröskel, i idrottarens telefon eller surfplatta: vad som ska göras i dag och resten av veckan. Ingen server, inget konto.*

<!-- toc -->
## Innehåll

- [Vad appen gör](#vad-appen-gör)
- [Hur schemat kommer till appen](#hur-schemat-kommer-till-appen)
- [Mina tider och planmotorn](#mina-tider-och-planmotorn)
- [Integritet](#integritet)
- [Bygga och köra lokalt](#bygga-och-köra-lokalt)
<!-- /toc -->

## Vad appen gör

Appen visar ett färdigt träningsschema från Tröskel. Den har fem vyer:

- *I dag*: dagens pass stort, med tid, zon, puls och tempo, varje del av passet och varför det görs. Under det kommer morgondagens pass.
- *Veckan*: sju kort från måndag till söndag, som en tavla på väggen. Dagens kort är markerat, vilodagarna är tomma och varje pass har sin zonfärg. Pilarna bläddrar vecka för vecka.
- *Schemat*: målen och testdagarna, blocken med varför de ligger där, en stapel per vecka med tiden i varje zon och zonerna med puls och tempo.
- *Mina tider*: hur länge och när idrottaren kan träna varje veckodag, och dagar som inte går.
- *Om appen*: språket, integriteten, licensen och knappen som tar bort all data på enheten.

Zonfärgerna är desamma som i Tröskel och på klockan: grå, blå, grön, orange och röd för zon 1 till 5. Appen följer telefonens språk (svenska eller engelska) och telefonens ljusa eller mörka läge. På en surfplatta i liggande läge står menyn till vänster och vyerna i två kolumner.

*Lägg passen i kalendern* (under *Veckan* och *Schemat*) laddar ner en kalenderfil med passen från i dag. Filen är samma som den Tröskel gör, och passen har samma nycklar, så att en ny fil uppdaterar passen i stället för att lägga in dem två gånger.

*Visa ett exempel* visar demolöparens schema, flyttat så att dess andra vecka är den här veckan.

## Hur schemat kommer till appen

Tröskel gör en länk av schemat:

```
https://generaltgul.github.io/troskel/app/#p=<data>
```

`<data>` är schemat som JSON, komprimerat med deflate och kodat som base64url. Allt efter `#` stannar i telefonen: webbläsaren skickar det aldrig till någon server. När appen har läst länken sparas schemat på enheten och tas bort ur adressfältet.

Samma data går också

- som QR-kod, som appen läser med kameran (*Läs en QR-kod*). Avkodningen sker i telefonen. Ett schema på tolv veckor ryms precis i en QR-kod; ett längre schema skickas som länk eller fil.
- som fil, `<namn>.troskel`, som är samma JSON i läsbar form (*Öppna en fil*).

Innehållet är det sparade schemat ur `plan/codec.py` (begäran och planen), idrottarens namn och varje zons puls och fart, uträknade ur testet när coachen skickar schemat. Appen har inga zonsystem själv: efter ett nytt test skickar coachen en ny länk. Typerna står i `app/src/model/types.ts`, och Python-sidan är `lopanalys.plan.link` (`stored_plan`, `plan_link`, `plan_file`, `read_link`).

## Mina tider och planmotorn

När idrottaren sparar nya tider räknar appen om schemat från i morgon med planmotorn, samma regler som i Tröskel. Motorn är ännu inte portad till appen. Till dess sparas tiderna på enheten, och appen säger att de används när coachen skickar ett nytt schema.

Gränssnittet mot motorn finns i `app/src/engine/index.ts`: `rebuild(stored, { slots, unavailable, from })` ger ett nytt sparat schema där passen före `from` är orörda.

## Integritet

Appen skickar inga data någonstans. Den har ingen spårning, ingen analys och inga anrop till andra tjänster. Schemat, tiderna och språkvalet sparas bara i webbläsarens lagring på enheten, och *Ta bort all data på enheten* tömmer den.

## Bygga och köra lokalt

Appen ligger i `app/` och byggs med Vite, TypeScript och Preact. Det kräver Node 24.

```bash
cd app
npm ci                 # beroendena, en gång
npm run dev            # utvecklingsserver på http://localhost:5173/troskel/app/
npm test               # proven (Vitest)
npm run build          # appen i app/dist/, för GitHub Pages under /troskel/app/
npm run preview        # den byggda appen på http://localhost:4173/troskel/app/
```

`TROSKEL_BASE=./ npm run build` bygger för appbutikerna, där appen inte ligger under `/troskel/app/`. `?view=week` (eller `today`, `plan`, `times`, `about`) i adressen öppnar en vy direkt, vilket är bra för skärmbilder.

Efter första besöket fungerar appen utan nät: service workern (`app/src/sw.js`) sparar alla appens filer. Bygget skriver in listan över filerna och en ny version varje gång något ändras.

Exempelschemat, provens fixturer och planens texter kommer från Python:

```bash
python app/scripts/make_fixtures.py
```

Det skriver `app/public/demo.troskel`, länken och kalenderfilerna i `app/test/fixtures/` och `app/src/i18n/plan.*.json`. Provet `tests/plan/test_link.py` stoppar när någon av dem inte stämmer med Python, till exempel efter en ändrad text i `plan.sv.json`. Appens egna texter står i `app/src/i18n/ui.sv.json` och `ui.en.json`.
