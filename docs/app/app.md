# Appen för idrottaren

*Schemat som coachen gör i Tröskel, i idrottarens telefon eller surfplatta: vad som ska göras i dag och resten av veckan. Ingen server, inget konto.*

<!-- toc -->
## Innehåll

- [Vad appen gör](#vad-appen-gör)
- [Hur schemat kommer till appen](#hur-schemat-kommer-till-appen)
  - [Från coach till idrottare](#från-coach-till-idrottare)
- [Bocka av passen](#bocka-av-passen)
  - [Veckan i siffror](#veckan-i-siffror)
  - [Rutan för dagen](#rutan-för-dagen)
  - [Säsongsremsan](#säsongsremsan)
- [Väggläget](#väggläget)
- [Mina tider och planmotorn](#mina-tider-och-planmotorn)
- [Integritet](#integritet)
- [Appen utan butiken](#appen-utan-butiken)
- [Bygga och köra lokalt](#bygga-och-köra-lokalt)

**Undersidor:**

- [Appen i App Store och Google Play](butikerna/butikerna.md)
<!-- /toc -->

## Vad appen gör

Appen visar ett färdigt träningsschema från Tröskel. Den har fem vyer:

- *I dag*: dagens pass stort, med tid, zon, puls och tempo (på cykeln watt, på SkiErgen watt och tid per 500 m, i simning tid per 100 m), varje del av passet och rutan för dagen: varför passet görs, hur det ska kännas och tips (se [Rutan för dagen](#rutan-för-dagen)). Under passet bockar idrottaren av det: *Gjort*, *Delvis* eller *Missat*. Ett styrkepass har ingen zon. En testdag har inte heller någon zon eller något eget mål, och kortet har en egen färg, violett, som inte är någon zons. Har dagen flera pass står de i tidsordning, och pass utan klocktid sist, som i utskriftens veckoblad. Under det kommer *+ Lägg till ett pass* och, i högra kolumnen på en surfplatta, morgondagens pass, veckan i siffror och säsongsremsan.
- *Veckan*: en rad per dag från måndag till söndag, byggd som hallskärmens vecka. Passen står som piller i zonens färg med idrottens ikon, längd, zon och klocktid, och ett pass utanför planen står som *+ namn* bredvid. Till höger på raden står dagens läge: *Gjort* när alla pass är gjorda, *Delvis* när något är gjort, *Missat* när bara missade pass är avbockade, *I dag* och *Vila*. Ett gjort pass har en bock i stället för ikonen, ett delvis gjort en streckad kant och ett missat är överstruket. Dagens rad är markerad. Rubriken mellan pilarna säger vilken vecka, vilket block, veckans tid och veckotyp, och pilarna bläddrar vecka för vecka. Ett tryck på en dag visar dagens pass med rutan för dagen, och fram till i dag går de att bocka av där. Bredvid veckan (under den på en telefon) står nästa lopp med dagar kvar och de följande loppen, och veckan i siffror. Säsongsremsan går i full bredd under.
- *Schemat*: målen och testdagarna (i ett schema med flera idrotter med idrotten som testas), blocken med varför de ligger där, en stapel per vecka med tiden i varje zon och zonerna med puls och tempo. I ett schema med flera idrotter står zonerna för varje idrott som har egna mål: huvudidrotten först, sedan till exempel cykelns watt och simningens tid per 100 m. I ett långt schema står veckans nummer under varannan eller var fjärde stapel.
- *Mina tider*: hur länge och när idrottaren kan träna varje veckodag, och dagar som inte går.
- *Om appen*: språket, [väggläget](#väggläget), integriteten, licensen och knappen som tar bort all data på enheten.

Zonfärgerna är desamma som i Tröskel och på klockan: grå, blå, grön, orange och röd för zon 1 till 5. Appen följer telefonens språk (svenska eller engelska) och telefonens ljusa eller mörka läge. På en surfplatta i liggande läge står menyn till vänster och vyerna i två kolumner. Från 900 punkters bredd står loppen och veckan i siffror i en högerkolumn bredvid veckan. På en smal telefon (ned till 320 punkter) står menyns fem namn på en rad, och ett mål som inte får plats bredvid ett annat får en egen rad.

*Lägg passen i kalendern* (under *Veckan* och *Schemat*) laddar ner en kalenderfil med passen från i dag. Filen är samma som den Tröskel gör, och passen har samma nycklar, så att en ny fil uppdaterar passen i stället för att lägga in dem två gånger.

*Visa ett exempel* visar demolöparens schema, flyttat så att dess andra vecka är den här veckan.

## Hur schemat kommer till appen

Tröskel gör en länk av schemat:

```
https://generaltgul.github.io/troskel/app/#p=<data>
```

`<data>` är JSON, komprimerat med deflate och kodat som base64url. Allt efter `#` stannar i telefonen: webbläsaren skickar det aldrig till någon server. När appen har läst länken sparas schemat på enheten och tas bort ur adressfältet.

Länken bär **underlaget**, inte passen (format 2, `short_plan` i `lopanalys.plan.link`). Appen bygger schemat själv med planmotorn, samma regler som i Tröskel, och får exakt det schema som Tröskel sparade. Underlaget är:

- begäran: formuläret, testen per idrott, tiderna och målen,
- schemats id, startdatum, när det skapades och gränsen för passen i ett rullande schema,
- motorns version (`v`),
- det i schemat som motorn inte ger tillbaka som det sparades (`x`): till exempel det tidigare testet efter ett nytt test,
- idrottarens namn och varje zons puls, fart, watt och simtempo, uträknade i Tröskel ur testen.

Nycklarna är korta, och det som har sitt standardvärde utelämnas. Demoschemat på tolv veckor blir en länk på cirka 460 tecken (hela schemat: 2 860), och ett schema med fyra idrotter, två pass samma dag och 30 veckor cirka 800 tecken (hela schemat: 3 650, eller 6 100 när alla veckor har pass).

Tröskel bygger schemat ur underlaget innan länken görs och jämför det med det sparade. Blir det inte samma schema, till exempel när ett pass har en status, bär länken hela schemat som förut (format 1). Appen läser båda formaten. Är länken gjord med en annan version av planmotorn bygger appen inget schema, utan visar raden *Schemat är gjort med en annan version av Tröskel. Uppdatera appen.*

Samma data går också

- som QR-kod, som appen läser med kameran (*Läs en QR-kod*). Avkodningen sker i telefonen. Även ett långt schema med flera idrotter ryms i en QR-kod.
- som fil, `<namn>.troskel`, som alltid har hela schemat i läsbar form (format 1, *Öppna en fil*).

Hela schemat är det sparade schemat ur `plan/codec.py` (begäran och planen), idrottarens namn och varje zons puls och fart (watt för cykel och SkiErg, `power_low_w` och `power_high_w`). Appen har inga zonsystem själv: efter ett nytt test skickar coachen en ny länk. Typerna står i `app/src/model/types.ts` och `app/src/model/short.ts`, och Python-sidan är `lopanalys.plan.link` (`stored_plan`, `short_plan`, `expand`, `plan_link`, `plan_file`, `read_link`). Vitest bygger schemat ur den korta länken för varje fixtur och jämför med Pythons sparade schema (`app/test/link.test.ts`).

### Från coach till idrottare

I Tröskel ger coachen idrottaren schemat på sidan *Träningsschema* ([handboken](../handbok/traningsschema/traningsschema.md#till-appen)):

- ***Till appen*** visar en ruta med QR-koden, länken med *Kopiera* och *Spara som fil* (`.troskel`). Idrottaren skannar koden med telefonens kamera eller öppnar länken i telefonen.
- ***Skicka till idrottaren*** mejlar schemat med raden *Öppna schemat i appen:* och länken, och med filen `.troskel` som bilaga bredvid PDF:en och kalenderfilen.

Båda kräver hälsodeklarationen och sparar schemat först, som utskriften och kalenderfilen. Länken, koden och mejlets länk bär underlaget (`link_payload`), och filen hela schemat. QR-koden ritas med segno (`src/lopanalys/share/qr.py`). Den största QR-koden rymmer 2 953 tecken med lägsta felrättningen. En längre länk får ingen kod, och rutan hänvisar till mejlet eller filen.

Efter *Förnya* eller ett nytt test ger *Till appen* det nya schemat, med samma schema-id och samma start. Appen har ett schema i taget: varje inläst schema ersätter det sparade (`setPlan` i `app/src/model/storage.ts`) och tar bort tider under *Mina tider* som inte har räknats in än.

## Bocka av passen

Idrottaren bockar av varje pass i *I dag* eller i dagens ruta under *Veckan*: **Gjort**, **Delvis** eller **Missat**. Ett andra tryck på samma val tar bort det. Vid *Delvis* frågar appen hur många minuter som blev gjorda, och tills idrottaren svarar räknas halva passet, i hela femminuter. Ett pass som inte har kommit än går inte att bocka av.

*+ Lägg till ett pass* lägger till ett pass utanför planen, med namn (till exempel yoga), idrott och minuter. Idrotterna är schemats, styrka och *Annat*. Passet står som *+ Yoga* i dagens lista och på veckans kort, och ett tryck på × tar bort det.

Allt sparas på enheten bredvid schemat, i samma lagring som *Mina tider* (`log` i `app/src/model/storage.ts`, se [Integritet](#integritet)), och logiken står i `app/src/model/log.ts`:

- Ett märke hör till passets nyckel (`<schema>-<åå><vv>-<dag>`). Planmotorn behåller nyckeln när den räknar om schemat med nya tider (samma schema-id och samma datum), och passen före `from` ligger kvar som de var, så märkena följer med. Ett nytt schema med samma id (efter *Förnya* i Tröskel) behåller också märkena, och ett schema med ett annat id tar bort dem (`keepFor`). Passen utanför planen ligger kvar, eftersom de är idrottarens och inte schemats.
- Har ett pass en status från Tröskel (`done`, `missed` eller `skipped` i schemat) visas den tills idrottaren bockar av passet själv.
- *Ta bort all data på enheten* tar bort märkena och passen utanför planen också.

### Veckan i siffror

Under *I dag* (den här veckan) och under *Veckan* (den visade veckan) står gjort mot planerat per idrott i timmar och minuter, till exempel *Löpning 0:48 av 2:50*. Huvudidrotten står först och styrka sist. Ett gjort pass räknas med hela sin tid, ett delvis gjort med minuterna idrottaren angav, och ett missat inte alls. Stapeln fylls mot veckans plan och blir grön när planen är nådd. Ett lopp har ingen planerad tid och ger ingen egen rad. Passen utanför planen står var för sig som *+ Yoga 0:40 extra* och räknas inte in i planens tid (`weekTotals`).

### Rutan för dagen

Dagens stora kort (och dagen under *Veckan*) har passets guide ur passbiblioteket i Tröskel ([Träningsplanen → Passbiblioteket](../TRANINGSPLAN.md#passbiblioteket)):

- **Varför** och **Så här**, som förut.
- **Känsla:** zonen och ansträngningen av 10 för de delar som bär passet, till exempel *Z4, ansträngning 7 av 10* (`feelText`, som `text.feel_line` i Python).
- **Tips:** noll till fyra per pass (`TIPS`).
- **Övningar att välja bland:** teknikpasset i simning har tio övningar (`DRILLS`).

Tabellerna `TIPS` och `DRILLS` och funktionen `feel` står både i `src/lopanalys/plan/library.py` och i `app/src/engine/library.ts`. Fixturerna (`guide` och `texts` i `engine.json.gz`) bär Pythons tabeller och texter, och `app/test/plan.test.ts` stoppar när appen visar något annat.

### Säsongsremsan

Under *I dag* står blocken fram till målet, en ruta per vecka med bredd efter veckor, och blockens korta namn ovanför (*Grund*, *Uppb.*, *Topp*, *Nedtr.*). De lätta veckorna är gröna, också testveckan, som är en lätt vecka med ett test. Loppveckan har appens orange färg, och ett streck visar i dag. Remsan går fram till nästa A-lopp, annars nästa lopp, annars det sista loppet, och utan lopp visar den hela schemat (`app/src/model/season.ts`).

## Väggläget

För en surfplatta på väggen, som en tavla i hallen. Under *Om appen* slår *Använd väggläget* på det, och valet sparas på enheten:

- **Skärmen hålls tänd** med Screen Wake Lock API (`navigator.wakeLock`). Webbläsaren släpper låset när appen inte syns, så appen tar det igen när den kommer tillbaka. Appen i butikerna använder samma API i sin webbvy: Capacitor har inget eget sätt att hålla skärmen tänd utan ett nytt tillägg, och appen tar inga nya beroenden för det. Saknar enheten API:t säger *Om appen* det, och då får skärmens tidsgräns ställas i enhetens inställningar.
- **Tillbaka till i dag:** efter 3 minuter utan tryck går appen tillbaka till *I dag*, och *Veckan* till den här veckan. En öppen dag stängs, och ett formulär som inte sparats töms. Varje tryck, tangent eller hjulrörelse börjar om tiden.
- **Datumet följer klockan:** appen läser datumet varje minut, så att *I dag* byter dag vid midnatt fast ingen rör plattan.

Logiken står i `app/src/model/wall.ts` (`idleTimer`, `keepAwake`), och proven i `app/test/log.test.ts` och `app/test/log_views.test.tsx`. På en riktig surfplatta är väggläget inte provat.

## Mina tider och planmotorn

När idrottaren sparar nya tider räknar appen om schemat från i morgon med planmotorn, samma regler som i Tröskel. Motorn i appen är en översättning till TypeScript av `src/lopanalys/plan/` (`periodize`, `engine`, `rolling` och passbiblioteket), en fil per Python-modul i `app/src/engine/`, och den provas mot Python-motorn (se nedan).

`rebuild(stored, { slots, unavailable, from })` i `app/src/engine/index.ts` ger ett nytt sparat schema:

- Underlaget får de nya tiderna, och motorn bygger hela schemat på nytt med samma schema-id. Blocken och målet ligger kvar, och veckotiden följer vad de nya tiderna rymmer. En ny dag får schemats alla idrotter, och en dag som fanns behåller sina.
- *Mina tider* har en rad per veckodag, dagens första tid. En andra tid samma dag (två pass samma dag, som coachen ger i Tröskel) ligger kvar som den var så länge dagen är på, och försvinner med dagen.
- En vecka som slutar före `from` ligger kvar som den sparades. I veckan som `from` faller i ligger passen före `from` kvar med sin status, och resten är nytt.
- I ett rullande schema står gränsen för passen kvar. Veckorna efter den blir skisser av det nya schemat, så att *Förnya* i Tröskel senare ger samma pass.
- De dagar som inte går stryks passen (loppen ligger kvar). Motorn flyttar dem inte till en annan dag, eftersom motorn i Python inte har dagar som faller bort.
- Går det inte att göra ett schema, till exempel utan någon dag för schemats idrotter, sparas tiderna på enheten som förut, och appen säger att de används när coachen skickar ett nytt schema.

Samma regel står i Python som `rebuild` i `app/scripts/make_fixtures.py`, skriven med Python-motorns egna funktioner. Med flera idrotter ger appen varje pass samma idrott som Tröskel och skriver idrottens namn först i titeln ([Träningsplanen → Flera idrotter](../TRANINGSPLAN.md#flera-idrotter)). Målen i puls och fart räknas inte om i appen: de beror på testet, inte på tiderna, och följer med schemat från Tröskel.

## Integritet

Appen skickar inga data någonstans. Den har ingen spårning, ingen analys och inga anrop till andra tjänster. Schemat, tiderna, det idrottaren bockat av, passen utanför planen och inställningarna (språket och väggläget) sparas bara på enheten, och *Ta bort all data på enheten* tömmer lagringen.

Var de sparas står i `app/src/model/storage.ts`:

- **Webbversionen** (GitHub Pages och på hemskärmen) sparar i webbläsarens `localStorage`.
- **Appen i butikerna** sparar med Capacitor Preferences, som är telefonens egen lagring för appar (UserDefaults på iOS, SharedPreferences på Android). iOS kan tömma en webbvys `localStorage` när telefonen har ont om plats, men inte Preferences. `Capacitor.isNativePlatform()` väljer lagringen.
- Preferences är asynkron. Appen läser därför in allt en gång vid starten (`openStore` i `main.tsx`) och läser sedan ur minnet och skriver igenom till Preferences, i ordning. Vyerna är synkrona som förut. Går Preferences inte att läsa startar appen med `localStorage` i stället.
- En installerad app som sparat i `localStorage` förut flyttar sina data till Preferences första gången, när Preferences är tom, och tömmer sedan `localStorage`. Misslyckas flytten ligger de kvar där.
- *Ta bort all data på enheten* tömmer både Preferences och `localStorage`.

Proven står i `app/test/storage.test.ts`. I en riktig telefon är lagringen inte provad ([PROV.md](../PROV.md#appen-i-telefonen)).

## Appen utan butiken

Appen behöver ingen butik för att provas. På Android installeras APK:n från en fast länk, och på iPhone och iPad läggs webbappen på hemskärmen.

**Android (telefon eller surfplatta):** den senaste APK:n från `main` ligger alltid på samma adress:

<https://github.com/GeneralTgul/troskel/releases/download/android-test/troskel.apk>

1. Öppna länken i Chrome på plattan. Ingen inloggning behövs. Chrome varnar för att filen kan vara skadlig; välj *Ladda ned ändå*.
2. Tryck på filen när den är hämtad. Första gången säger Android att Chrome inte får installera okända appar: tryck på *Inställningar*, slå på *Tillåt från den här källan* och gå tillbaka.
3. Tryck på *Installera*. Play Protect kan fråga om appen ska skannas; *Installera ändå* går bra, eftersom appen kommer från vårt eget bygge.
4. Slå av *Tillåt från den här källan* igen under *Inställningar → Appar → Chrome → Installera okända appar*, så att bara den här filen fick gå in.

En ny version installeras på samma sätt, ovanpå den gamla, och appens data ligger kvar: testbyggena skrivs under med samma nyckel, `app/android/app/troskel-test.p12`, som bara skriver under testbyggen, aldrig AAB-filen till Play. På en platta som visar en kiosk-app (som Fully Kiosk) lämnas kiosken först, så att Chrome och installationen syns.

Länken pekar på förhandsreleasen `android-test`, som jobbet *Android* i bygget *App* skriver över vid varje körning på `main` eller en tagg `app-v…`. Releasens text säger version, körning och commit. Varje körning sparar också APK:n och AAB-filen som artefakten `android-<version>-<körning>`, men den kräver inloggning på GitHub och kommer som zip.

**iPhone och iPad:** öppna <https://generaltgul.github.io/troskel/app/> i Safari, tryck på *Dela* (rutan med pilen) och *Lägg till på hemskärmen*. Appen startar sedan från ikonen, utan adressfält. Att den fungerar utan nät är inte provat i en telefon ([PROV.md](../PROV.md#appen-i-telefonen)). Den byggda iOS-appen kräver Xcode på en Mac eller ett utvecklarkonto hos Apple (TestFlight).

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
