# Appen för idrottaren

*Schemat som coachen gör i Tröskel, i idrottarens telefon eller surfplatta: vad som ska göras i dag och resten av veckan. Ingen server, inget konto.*

<!-- toc -->
## Innehåll

- [Vad appen gör](#vad-appen-gör)
- [Hur schemat kommer till appen](#hur-schemat-kommer-till-appen)
- [Mina tider och planmotorn](#mina-tider-och-planmotorn)
- [Integritet](#integritet)
- [Bygga och köra lokalt](#bygga-och-köra-lokalt)

**Undersidor:**

- [Appen i App Store och Google Play](butikerna/butikerna.md)
<!-- /toc -->

## Vad appen gör

Appen visar ett färdigt träningsschema från Tröskel. Den har fem vyer:

- *I dag*: dagens pass stort, med tid, zon, puls och tempo (på cykeln watt, på SkiErgen watt och tid per 500 m, i simning tid per 100 m), varje del av passet och varför det görs. Ett styrkepass har ingen zon. En testdag har inte heller någon zon eller något eget mål, och kortet har appens accentfärg. Har dagen flera pass står de i tidsordning, och pass utan klocktid sist, som i utskriftens veckoblad. Under det kommer morgondagens pass.
- *Veckan*: sju kort från måndag till söndag, som en tavla på väggen. Dagens kort är markerat, vilodagarna är tomma och varje pass har sin zonfärg. En dag med två pass, till exempel löpning och styrka, har färgen från passet som har en zon. Pilarna bläddrar vecka för vecka.
- *Schemat*: målen och testdagarna (i ett schema med flera idrotter med idrotten som testas), blocken med varför de ligger där, en stapel per vecka med tiden i varje zon och zonerna med puls och tempo. I ett schema med flera idrotter står zonerna för varje idrott som har egna mål: huvudidrotten först, sedan till exempel cykelns watt och simningens tid per 100 m. I ett långt schema står veckans nummer under varannan eller var fjärde stapel.
- *Mina tider*: hur länge och när idrottaren kan träna varje veckodag, och dagar som inte går.
- *Om appen*: språket, integriteten, licensen och knappen som tar bort all data på enheten.

Zonfärgerna är desamma som i Tröskel och på klockan: grå, blå, grön, orange och röd för zon 1 till 5. Appen följer telefonens språk (svenska eller engelska) och telefonens ljusa eller mörka läge. På en surfplatta i liggande läge står menyn till vänster och vyerna i två kolumner. Veckans sju kort står i en rad från 900 punkters bredd, och i två rader på en smalare surfplatta. På en smal telefon (ned till 320 punkter) står menyns fem namn på en rad, och ett mål som inte får plats bredvid ett annat får en egen rad.

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

Innehållet är det sparade schemat ur `plan/codec.py` (begäran och planen), idrottarens namn och varje zons puls och fart (watt för cykel och SkiErg, `power_low_w` och `power_high_w`), uträknade ur testet när coachen skickar schemat. Appen har inga zonsystem själv: efter ett nytt test skickar coachen en ny länk. Typerna står i `app/src/model/types.ts`, och Python-sidan är `lopanalys.plan.link` (`stored_plan`, `plan_link`, `plan_file`, `read_link`).

## Mina tider och planmotorn

När idrottaren sparar nya tider räknar appen om schemat från i morgon med planmotorn, samma regler som i Tröskel. Motorn i appen är en översättning till TypeScript av `src/lopanalys/plan/` (`periodize`, `engine`, `rolling` och passbiblioteket), en fil per Python-modul i `app/src/engine/`, och den provas mot Python-motorn (se nedan).

`rebuild(stored, { slots, unavailable, from })` i `app/src/engine/index.ts` ger ett nytt sparat schema:

- Underlaget får de nya tiderna, och motorn bygger hela schemat på nytt med samma schema-id. Blocken och målet ligger kvar, och veckotiden följer vad de nya tiderna rymmer. En ny dag får schemats alla idrotter, och en dag som fanns behåller sina.
- En vecka som slutar före `from` ligger kvar som den sparades. I veckan som `from` faller i ligger passen före `from` kvar med sin status, och resten är nytt.
- I ett rullande schema står gränsen för passen kvar. Veckorna efter den blir skisser av det nya schemat, så att *Förnya* i Tröskel senare ger samma pass.
- De dagar som inte går stryks passen (loppen ligger kvar). Motorn flyttar dem inte till en annan dag, eftersom motorn i Python inte har dagar som faller bort.
- Går det inte att göra ett schema, till exempel utan någon dag för schemats idrotter, sparas tiderna på enheten som förut, och appen säger att de används när coachen skickar ett nytt schema.

Samma regel står i Python som `rebuild` i `app/scripts/make_fixtures.py`, skriven med Python-motorns egna funktioner. Med flera idrotter ger appen varje pass samma idrott som Tröskel och skriver idrottens namn först i titeln ([Träningsplanen → Flera idrotter](../TRANINGSPLAN.md#flera-idrotter)). Målen i puls och fart räknas inte om i appen: de beror på testet, inte på tiderna, och följer med schemat från Tröskel.

## Integritet

Appen skickar inga data någonstans. Den har ingen spårning, ingen analys och inga anrop till andra tjänster. Schemat, tiderna och språkvalet sparas bara på enheten, och *Ta bort all data på enheten* tömmer lagringen.

Var de sparas står i `app/src/model/storage.ts`:

- **Webbversionen** (GitHub Pages och på hemskärmen) sparar i webbläsarens `localStorage`.
- **Appen i butikerna** sparar med Capacitor Preferences, som är telefonens egen lagring för appar (UserDefaults på iOS, SharedPreferences på Android). iOS kan tömma en webbvys `localStorage` när telefonen har ont om plats, men inte Preferences. `Capacitor.isNativePlatform()` väljer lagringen.
- Preferences är asynkron. Appen läser därför in allt en gång vid starten (`openStore` i `main.tsx`) och läser sedan ur minnet och skriver igenom till Preferences, i ordning. Vyerna är synkrona som förut. Går Preferences inte att läsa startar appen med `localStorage` i stället.
- En installerad app som sparat i `localStorage` förut flyttar sina data till Preferences första gången, när Preferences är tom, och tömmer sedan `localStorage`. Misslyckas flytten ligger de kvar där.
- *Ta bort all data på enheten* tömmer både Preferences och `localStorage`.

Proven står i `app/test/storage.test.ts`. I en riktig telefon är lagringen inte provad ([PROV.md](../PROV.md#appen-i-telefonen)).

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

Det skriver `app/public/demo.troskel`, länken och kalenderfilerna i `app/test/fixtures/` och `app/src/i18n/plan.*.json`. Provet `tests/plan/test_link.py` stoppar när någon av dem inte stämmer med Python, till exempel efter en ändrad text i `plan.sv.json`.

Det skriver också `app/test/fixtures/engine.json.gz`: scheman som Python-motorn bygger för demolöparna (nybörjare, motionär och vältränad, 5 till 20 veckor, med och utan mål, olika tider, rullande scheman och felen), förnyelser av ett rullande schema och `rebuild` med tidsändringar. `app/test/engine.test.ts` jämför motorn i appen med varje fall i sin helhet. Ändras motorn i Python stoppar `tests/plan/test_link.py` tills fixturerna skrivs om, och sedan stoppar Vitest tills TypeScript-motorn följer med. Python-motorn räknar med flyttal, så TypeScript-motorn gör räkneoperationerna i samma ordning och avrundar som Python (`app/src/engine/py.ts`). Fixturerna skrivs med Python 3.12 eller senare, där `sum` lägger ihop flyttal med kompensation. Appens egna texter står i `app/src/i18n/ui.sv.json` och `ui.en.json`.
