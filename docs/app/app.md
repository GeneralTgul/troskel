# Appen för idrottaren

*Schemat som coachen gör i Tröskel, i idrottarens telefon eller surfplatta: vad som ska göras i dag och resten av veckan. Ingen server, inget konto.*

<!-- toc -->
## Innehåll

- [Vad appen gör](#vad-appen-gör)
  - [Spara en fil](#spara-en-fil)
- [Hur schemat kommer till appen](#hur-schemat-kommer-till-appen)
  - [Från coach till idrottare](#från-coach-till-idrottare)
  - [Från idrottare till coach](#från-idrottare-till-coach)
  - [Ett nytt test ger ett nytt schema](#ett-nytt-test-ger-ett-nytt-schema)
  - [Ett schema utan coach](#ett-schema-utan-coach)
  - [Egna värden](#egna-värden)
  - [Ändra ett pass](#ändra-ett-pass)
- [Bocka av passen](#bocka-av-passen)
  - [Veckan i siffror](#veckan-i-siffror)
  - [Rutan för dagen](#rutan-för-dagen)
  - [Säsongsremsan](#säsongsremsan)
- [Hälsodatan](#hälsodatan)
  - [Länken med hälsodata](#länken-med-hälsodata)
  - [Passen bockas av av sig själva](#passen-bockas-av-av-sig-själva)
  - [Dagsformen](#dagsformen)
  - [Var hälsodatan kommer ifrån](#var-hälsodatan-kommer-ifrån)
  - [Health Connect i Android-appen](#health-connect-i-android-appen)
- [Väggläget](#väggläget)
- [Mina tider och planmotorn](#mina-tider-och-planmotorn)
- [En kopia av schemat](#en-kopia-av-schemat)
- [Integritet](#integritet)
- [Appen utan butiken](#appen-utan-butiken)
- [Bygga och köra lokalt](#bygga-och-köra-lokalt)

**Undersidor:**

- [Appen i App Store och Google Play](butikerna/butikerna.md)
- [Kom igång med appen](kom-igang/kom-igang.md)
<!-- /toc -->

## Vad appen gör

Appen visar ett färdigt träningsschema från Tröskel. Den har fem vyer:

- *I dag*: dagens pass stort, med tid, zon, puls och tempo (på cykeln watt, på SkiErgen watt och tid per 500 m, i simning tid per 100 m), varje del av passet och rutan för dagen: varför passet görs, hur det ska kännas och tips (se [Rutan för dagen](#rutan-för-dagen)). Under passet bockar idrottaren av det: *Gjort*, *Delvis* eller *Missat*. Ett styrkepass har ingen zon. En testdag har inte heller någon zon eller något eget mål, och kortet har en egen färg, violett, som inte är någon zons. Har dagen flera pass står de i tidsordning, och pass utan klocktid sist, som i utskriftens veckoblad. Under det kommer *+ Lägg till ett pass* och, i högra kolumnen på en surfplatta, morgondagens pass, veckan i siffror och säsongsremsan.
- *Veckan*: en rad per dag från måndag till söndag, byggd som hallskärmens vecka. Passen står som piller i zonens färg med idrottens ikon, längd, zon och klocktid, och ett pass utanför planen står som *+ namn* bredvid. Till höger på raden står dagens läge: *Gjort* när alla pass är gjorda, *Delvis* när något är gjort, *Missat* när bara missade pass är avbockade, *I dag* och *Vila*. Ett gjort pass har en bock i stället för ikonen, ett delvis gjort en streckad kant och ett missat är överstruket. Dagens rad är markerad. Rubriken mellan pilarna säger vilken vecka, vilket block, veckans tid och veckotyp, och pilarna bläddrar vecka för vecka. Ett tryck på en dag visar dagens pass med rutan för dagen, och fram till i dag går de att bocka av där. Bredvid veckan (under den på en telefon) står nästa lopp med dagar kvar och de följande loppen, och veckan i siffror. Säsongsremsan går i full bredd under.
- *Schemat*: målen och testdagarna (i ett schema med flera idrotter med idrotten som testas), blocken med varför de ligger där, en stapel per vecka med tiden i varje zon och zonerna med puls och tempo. I ett schema med flera idrotter står zonerna för varje idrott som har egna mål: huvudidrotten först, sedan till exempel cykelns watt och simningens tid per 100 m. Under zonerna står vad de bygger på: *Uppmätt i test <dag>* eller *Egna värden*, och knappen *Ändra mina värden* ([Egna värden](#egna-värden)). Efter målen står *Nästa test* (#778, `app/src/model/nexttest.ts`): dagen coachen valt i Tröskel, annars förvalet 8–12 veckor efter testet (samma regel som `plan/next_test.py`), och en rad om att passens mål bygger på testet från <dag> (eller egna värden). Efter dagen blir raden en ruta: målen stämmer allt sämre, och nya mål kräver ett nytt test eller nya egna värden. `app/test/nexttest.test.tsx` jämför med Python (`app/test/fixtures/nexttest.json.gz`, `nexttest_fixtures`) och provar att dagen följer med efter nya tider, *Förnya*, ändrade pass, egna värden, den korta länken och filen till coachen. I ett långt schema står veckans nummer under varannan eller var fjärde stapel.
- *Mina tider*: hur länge och när idrottaren kan träna varje veckodag, och dagar som inte går.
- *Om appen*: språket, *Gör ett schema själv* ([Ett schema utan coach](#ett-schema-utan-coach)), [väggläget](#väggläget), [en kopia av schemat](#en-kopia-av-schemat), integriteten, licensen och knappen som tar bort all data på enheten.

Zonfärgerna är desamma som i Tröskel och på klockan: grå, blå, grön, orange och röd för zon 1 till 5. Appen följer telefonens språk (svenska eller engelska) och telefonens ljusa eller mörka läge. På en surfplatta i liggande läge står menyn till vänster och vyerna i två kolumner. Från 900 punkters bredd står loppen och veckan i siffror i en högerkolumn bredvid veckan. På en smal telefon (ned till 320 punkter) står menyns fem namn på en rad, och ett mål som inte får plats bredvid ett annat får en egen rad.

*Lägg passen i kalendern* (under *Veckan* och *Schemat*) laddar ner en kalenderfil med passen från i dag. Filen är samma som den Tröskel gör, och passen har samma nycklar, så att en ny fil uppdaterar passen i stället för att lägga in dem två gånger.

### Spara en fil

Appen gör tre filer: kalenderfilen, [kopian](#en-kopia-av-schemat) och [schemat till coachen](#från-idrottare-till-coach). `app/src/model/save.ts` sparar dem på två sätt:

- **I webbläsaren och på hemskärmen** laddas filen ner, som en länk med `download` till en blob.
- **I apparna från butikerna och i APK:n** skrivs filen i appens cache (`@capacitor/filesystem`) och lämnas till telefonens delningsruta (`@capacitor/share`), där idrottaren sparar den i Filer eller skickar den vidare. En webbvy i en app har inga nedladdningar: knappen gjorde förut ingenting där, och appen sa ändå att filen var sparad.

Stängs delningsrutan utan att filen sparats säger appen *Filen sparades inte*, och dagen för den senaste kopian ändras inte. I en riktig telefon är det inte provat ([PROV.md](../PROV.md#appen-i-telefonen)).

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

- som QR-kod, som appen läser med kameran (*Läs en QR-kod*). Avkodningen sker i telefonen. Appen använder webbläsarens egen läsare (`BarcodeDetector`) bara när den säger sig läsa QR-koder, och annars jsQR som följer med appen (`app/src/model/qr.ts`). Misslyckas webbläsarens läsare, som på en Android-enhet utan Google Play-tjänsternas modul, tar jsQR över. Även ett långt schema med flera idrotter ryms i en QR-kod.
- som fil, `<namn>.troskel`, som alltid har hela schemat i läsbar form (format 1, *Öppna en fil*).

Hela schemat är det sparade schemat ur `plan/codec.py` (begäran och planen), idrottarens namn och varje zons puls och fart (watt för cykel och SkiErg, `power_low_w` och `power_high_w`). Målen för ett test räknas i Tröskel, och efter ett nytt test skickar coachen en ny länk. Bara för idrottarens [egna värden](#egna-värden) räknar appen fram målen själv. Typerna står i `app/src/model/types.ts` och `app/src/model/short.ts`, och Python-sidan är `lopanalys.plan.link` (`stored_plan`, `short_plan`, `expand`, `plan_link`, `plan_file`, `read_link`). Vitest bygger schemat ur den korta länken för varje fixtur och jämför med Pythons sparade schema (`app/test/link.test.ts`).

### Från coach till idrottare

I Tröskel ger coachen idrottaren schemat på sidan *Träningsschema* ([handboken](../handbok/traningsschema/traningsschema.md#till-appen)):

- ***Till appen*** visar en ruta med QR-koden, länken med *Kopiera* och *Spara som fil* (`.troskel`). Idrottaren skannar koden med telefonens kamera eller öppnar länken i telefonen.
- ***Skicka till idrottaren*** mejlar schemat med raden *Öppna schemat i appen:* och länken, och med filen `.troskel` som bilaga bredvid PDF:en och kalenderfilen.

Båda kräver hälsodeklarationen och sparar schemat först, som utskriften och kalenderfilen. Länken, koden och mejlets länk bär underlaget (`link_payload`), och filen hela schemat. QR-koden ritas med segno (`src/lopanalys/share/qr.py`). Den största QR-koden rymmer 2 953 tecken med lägsta felrättningen. En längre länk får ingen kod, och rutan hänvisar till mejlet eller filen.

Efter *Förnya* eller ett nytt test ger *Till appen* det nya schemat, med samma schema-id och samma start. Appen har ett schema i taget: varje inläst schema ersätter det sparade (`setPlan` i `app/src/model/storage.ts`) och tar bort tider under *Mina tider* som inte har räknats in än.

### Från idrottare till coach

Inför ett nytt test kan coachen läsa in idrottarens schema i Tröskel, så att testet ger nya mål i idrottarens eget schema och inte i det som en gång skickades. Under *Om* finns rutan ***Skicka schemat till din coach*** (`app/src/views/Share.tsx`), när appen har ett eget schema (inte exemplet):

- **Vad som följer med** står i rutan: schemat som det ser ut i telefonen, med idrottarens tider och det appens motor räknat om, det som är avbockat (också det [hälsodatan](#passen-bockas-av-av-sig-själva) bockat av, märkt `auto`), passen utanför schemat, tider som väntar under *Mina tider* och dagen för exporten. Nätterna (sömn, vilopuls, HRV) och klockans pass stannar i telefonen.
- **Idrottarens godkännande:** knapparna *Spara som fil* och *Kopiera länken* är grå tills idrottaren kryssat i *Jag vill dela det här med min coach*.
- **Filen** heter `troskel-<namn>-<dag>.troskel` och är JSON i formatet `troskel.athlete` (format 1, `app/src/model/share.ts`). **Länken** är `…/troskel/app/#a=<data>`, kodad som schemats länk (deflate och base64url). Samma fil och länk är idrottarens egen kopia ([En kopia av schemat](#en-kopia-av-schemat)), och appen läser in dem som en kopia. Delen efter `#` skickas aldrig till en server. Länken är längre än schemats, eftersom den bär hela schemat.
- **Appen skickar inget själv:** idrottaren väljer vem filen eller länken går till, till exempel i ett meddelande eller ett mejl.

I Tröskel läser coachen in det med *Läs in från appen* på sidan *Träningsschema* ([handboken](../handbok/traningsschema/traningsschema.md#läs-in-schemat-från-appen)). Tröskel läser formatet med `src/lopanalys/plan/phone.py`.

### Ett nytt test ger ett nytt schema

Passen i telefonen ändras bara av ett nytt test eller av idrottarens eget val under *Mina tider*, aldrig av sig själva. Appen räknar inte fram veckorna efter gränsen för passen, och samma sparade schema ser likadant ut hur långt senare appen än öppnas. Kedjan efter ett nytt test:

1. Har idrottaren ändrat sina tider eller bockat av pass, läser coachen först in schemat från telefonen ([Från idrottare till coach](#från-idrottare-till-coach)). Då bygger resten av kedjan på idrottarens schema.
2. Tröskel lägger resten av schemat på testet (*Lägg om schemat*, `apply_retest` i `src/lopanalys/ui/services.py`): från testdagen får passen i testets idrott de nya målen, passen före testdagen behåller de gamla (`targets_log`), och inget pass byter dag. De närmaste veckorna får sina pass, som vid *Förnya*.
3. Coachen ger idrottaren en ny länk, QR-kod eller fil med *Till appen* eller *Skicka till idrottaren*. Den har samma schema-id och samma nycklar på passen.
4. Telefonen byter schemat. Det idrottaren bockat av, passen utanför planen och hälsodatan ligger kvar (`keepFor` i `app/src/model/log.ts`).
5. Läste coachen in schemat från telefonen har det nya schemat idrottarens tider, också de som väntade under *Mina tider*, och telefonen behåller inga tider. Annars har det nya schemat coachens tider. Idrottarens tider (de inräknade, eller de som väntar) ligger då kvar under *Mina tider* med en rad om det, och *Spara* lägger det nya schemat på dem, med det nya testets mål (`keptTimes` i `app/src/model/times.ts`). Appen gör det inte av sig själv. Ett schema som är skrivet utanför motorn (`source`, se [Mina tider och planmotorn](#mina-tider-och-planmotorn)) har inga egna tider att spara, och ett schema med ett annat id behåller inga tider.

Proven: `tests/plan/test_retest_chain.py` går hela vägen med två tester på samma idrottare, och `app/test/retest_phone.test.tsx` provar telefonens sida. Vägen från telefonen och tillbaka provas med en fixtur ur `app/scripts/make_fixtures.py` (`athlete.troskel` och `athlete.back.link.txt`): `app/test/share.test.tsx` på telefonens sida, `tests/plan/test_phone.py` och `tests/ui/test_plan_phone.py` i Tröskel. I en riktig telefon är kedjan inte provad ([PROV.md](../PROV.md#appen-i-telefonen)).

### Ett schema utan coach

Idrottaren kan göra ett schema i appen utan coach och utan test: ***Gör ett schema själv*** på välkomstsidan och under *Om* (`app/src/views/Request.tsx`, `app/src/model/request.ts`). Formuläret har samma frågor som coachens formulär på sidan *Träningsschema* i Tröskel:

- idrotterna, med roll (huvudidrott, stödjande, underhåll) när de är flera, och styrkepass i veckan (0–3),
- nivån och minuter i veckan de senaste veckorna,
- minuter och klockslag per veckodag (20–480 minuter), och med flera idrotter vilken idrott dagen är till,
- ett lopp med dag, distans och måltid, och delmål före det (viktigt lopp eller träningslopp), eller ett antal veckor,
- dagen då schemat börjar, och namnet (frivilligt).

Appens planmotor bygger schemat i telefonen, med ett eget id (`app-` och åtta tecken). Schemat rullar som coachens (#789): de fyra närmaste veckorna får sina pass och resten är en skiss. När appen startar, och när dagen byts i väggläget, förnyar den själv ett eget schema när det är dags (`renewOwn` i `request.ts`, `due` och `rollForward` i `app/src/engine/rolling.ts`), med samma regler och samma pass som *Förnya* i Tröskel. De nya veckorna får passen efter schemats underlag som det är då, med idrottarens egna värden och tester, och flyttade, bytta och kortade pass läggs på igen. Ett schema från en coach förnyas av coachen, och ett eget schema som gjordes före #789 har pass i alla veckor och behöver ingen förnyelse. Utan test blir passen i zoner och efter ansträngning, utan puls och tempo, som i Tröskel utan test. Har telefonen redan ett schema (inte exemplet) står dess svar i formuläret, och dess tester och mål i puls och tempo följer med. Det nya schemat ersätter det gamla efter en fråga, och det idrottaren bockat av ligger kvar.

Begäran följer reglerna i coachens formulär (`collect` i `src/lopanalys/ui/views/plan.py`), så att schemat blir det Tröskel hade gjort av samma svar och går att läsa in i Tröskel med *Läs in från appen* ([Från idrottare till coach](#från-idrottare-till-coach)). Samma formulär byggs i Python i `app/scripts/make_fixtures.py` (`form_request`, `form_from_plan`), och `app/test/request.test.tsx` jämför appens begäran och schema med Pythons (`app/test/fixtures/request.json.gz`).

Formuläret har inte en andra tid samma dag, fasta hårda pass eller andelar per idrott. Ett schema från Tröskel som har dem tappar dem när idrottaren gör ett nytt schema av det.

### Egna värden

Utan test kan idrottaren fylla i det den vet om sig själv, så att passen får puls, tempo eller watt: under *Dina värden* i *Gör ett schema själv*, och med *Ändra mina värden* under *Schemat* för det schema telefonen har (`app/src/views/Values.tsx`, `app/src/model/values.ts`). Allt är frivilligt, idrott för idrott:

- **Löpning:** maxpuls, vilopuls, puls vid LT1 och LT2, och tempot vid LT1 och LT2 i min/km.
- **Cykel:** samma pulser, watt vid LT1 och FTP (watten vid LT2). Med bara FTP och maxpuls får sweet spot-passen sin watt (88–94 % av FTP).
- **SkiErg:** samma pulser och watt vid LT1 och LT2.
- **Skidor:** pulserna. Skidorna har ingen egen belastning och får puls och ansträngning, som när de lånar SkiErg-testet.
- **Simning:** CSS-tempot per 100 m.

Med båda tröskelpulserna får zonerna idrottarens egna gränser, och tempot eller watten vid trösklarna följer med. Med bara maxpulsen räknas zonerna ur den (uppskattade, som i Tröskel efter ett maxpulstest). Fälten kontrolleras: pulserna ska stiga från vilopuls till LT1, LT2 och maxpuls, LT2 ska vara snabbare än LT1, och tempot skrivs som `5:20`.

Regler:

- **Egna värden märks** `source: "self"` i schemats `capacity`, utan testdag, och visas som *Egna värden*. Ett test från Tröskel visas som *Uppmätt i test <dag>*. Formatet är detsamma som för ett test, så schemat går att läsa in i Tröskel med *Läs in från appen*, och Tröskel räknar samma mål ur dem.
- **Egna värden ersätter aldrig ett test utan att idrottaren väljer det.** En idrott med ett test från Tröskel visar testet, och fälten kommer först efter *Skriv in egna värden i stället* och en fråga. *Behåll testet* tar bort raden igen.
- **Ett test i Tröskel ersätter egna värden i sin idrott.** Kommer ett nytt schema från coachen med samma id och ett test i idrotten, gäller testet. Egna värden i en idrott som schemat från coachen saknar test i ligger kvar (`keptOwnValues`). I Tröskel gör *Lägg om schemat* samma sak (`resolve.retest`), och ett SkiErg-test ersätter också skidornas egna värden.
- **Ett test jämförs aldrig med egna värden.** *Mot schemat* i Tröskels rapport (#778) tar dagen för det förra testet ur Tröskels egen databas, inte ur schemats `capacity`, så egna värden kommer aldrig in i jämförelsen. Finns inget tidigare test i Tröskel blir det ingen jämförelse.
- *Ändra mina värden* lägger värdena på samma schema: samma veckor och pass, nya mål i hela schemat. Det gamla testet i idrotten går inte att få tillbaka i appen; coachen skickar det med nästa länk.

Zonerna räknas i appen med en översättning av `resolve.targets`, `band_targets` och `swim_targets` och klockans fem zoner ur `zone_systems.convert` (`app/src/engine/zones.ts`), med Pythons avrundning. `app/scripts/make_fixtures.py` skriver `app/test/fixtures/zones.json.gz` (`zones_fixtures`: kapaciteter med målen Python ger, målen per idrott för flera begäran, ifyllda fält och ett schema med egna värden), och `app/test/values.test.ts` jämför. Farten ur pulsen i ett zontest utan laktat (`hr_zones.zone_speeds`) finns inte i appen: för ett sådant test behåller appen målen från Tröskel.

### Ändra ett pass

I dagens ruta under *Veckan* kan idrottaren ändra ett pass som inte har kommit än och inte är bockat: ***Ändra passet*** (`app/src/views/Adjust.tsx`, `app/src/model/adjust.ts`).

- **Flytta till** en annan dag i samma vecka, från i dag. Passet får klockslaget för den nya dagens tid under *Mina tider*, eller ingen tid.
- **Byt med** ett annat pass i veckan: de två passen byter dag.
- **Korta till** en kortare längd, i hela femminuter ner till det kortaste passet tillåter (ett intervallpass förlorar hela intervaller, uppvärmningen och nedvarvningen minst 5 minuter).
- **Ångra ändringen** ger passet som motorn gjorde det. Under passet står *Flyttat från …* eller *Kortat från …*.

Lopp och omtest går inte att ändra, och inte heller exempelschemat eller ett schema som är skrivet utanför motorn.

Regler (#776, #777):

- **Ändringarna sparas som en lista ovanpå schemat** (`plan.edits`: passets nyckel, ny dag, nya minuter), och passet behåller sin nyckel, så det avbockade följer med. Både appens motor (`app/src/engine/edits.ts`) och Tröskels (`plan/edits.py`) lägger listan på veckorna igen efter *Spara* under *Mina tider* (`rebuild`), *Förnya* (`rollForward`, `roll_forward`) och ett nytt test, så ändringarna försvinner inte.
- **Ett nytt schema från coachen med samma id** (ett nytt test) får idrottarens ändringar för de pass som det inte har egna ändringar för (`keptEdits`). Ett annat schema börjar utan ändringar.
- **Till coachen:** listan följer med *Skicka schemat till din coach* (#781), och Tröskel behåller den när coachen lägger om schemat efter ett nytt test, förnyar det eller ändrar formuläret för samma schema (`services.with_saved_edits`).
- `app/scripts/make_fixtures.py` skriver `app/test/fixtures/edits.json.gz` (`edits_fixtures`: alla mallar kortade till flera längder, scheman med flyttade, bytta och kortade pass, och samma scheman efter nya tider, *Förnya*, den korta länken och coachens nya schema), och `app/test/edits.test.tsx` jämför.

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

## Hälsodatan

Appen kan få passen som klockan spelade in och nattens värden (sömn, vilopuls, HRV) från telefonens hälsodata: ur en länk, eller i Android-appen direkt ur [Health Connect](#health-connect-i-android-appen). Med dem bockar den av passen själv och säger hur dagsformen är. Allt stannar på enheten, som schemat.

### Länken med hälsodata

Hälsodatan kommer i en länk, på samma sätt som schemat:

```
https://generaltgul.github.io/troskel/app/#h=<data>
```

`<data>` är JSON, kodat som base64url, som vanlig base64 eller URL-kodat. Den är inte komprimerad, så att en genväg i iPhone kan göra den. När appen har läst länken sparas datan och tas bort ur adressfältet.

```json
{"kind": "troskel-health", "format": 1, "read": "2026-11-25T07:10:00+01:00",
 "workouts": [{"sport": "running", "start": "2026-11-24T18:02:00+01:00", "minutes": 48, "hr": 142, "hr_max": 171}],
 "days": [{"date": "2026-11-25", "sleep_h": 6.4, "rhr": 48, "hrv": 62}]}
```

- **Passen:** idrotten med Apple Hälsas, Health Connects eller Stravas namn (`running`, `HKWorkoutActivityTypeCycling`, `EXERCISE_TYPE_SWIMMING_POOL`, `VirtualRide`), starten i lokal tid och minuterna. Snitt- och maxpuls får saknas. Idrotterna översätts till schemats (`sportOf`), och det appen inte känner blir *Annat*.
- **Dagarna:** datumet är dagen idrottaren vaknade, och nattens sömn hör till den. Flera rader samma dag läggs ihop: sömnen adderas (nattens delar) och HRV och vilopuls blir snittet. En genväg kan alltså skriva en rad per mätning.
- En ny länk läggs över den sparade: en dags värden ersätts, och två pass som överlappar med mer än halva det kortare (klockan och Zwift samma pass) blir ett, det längre. Appen sparar de senaste 42 dagarna.

Logiken står i `app/src/model/health.ts`, vyerna i `app/src/views/health.tsx` och proven i `app/test/health.test.tsx`.

### Passen bockas av av sig själva

Ett pass i schemat är gjort när hälsodatan har ett pass i samma idrott (`autoMarks`, samma regler som Tröskels hallskärm):

1. Samma dag: det längsta passet räknas, så att uppvärmningen inte gör det.
2. Annars samma idrott en annan dag samma vecka, om passet var minst 20 minuter och inte redan räknats. Den närmaste dagen vinner, och vid lika den senare (lördagens långpass på söndagen). Bara dagar som har varit, och aldrig ett lopp.
3. SkiErg och skidor räknas som samma idrott. Ett lopp räknas med sin idrott.

Under passet står *Från Hälsa*, eller *Från Hälsa, gjort 24/11* när passet gjordes en annan dag. Hälsodatan sätter aldrig *Missat*. En bock som idrottaren satt själv vinner, och appen sparar bara idrottarens egna: de automatiska räknas om varje gång. *Veckan i siffror* räknar dem som gjorda.

### Dagsformen

Överst under *I dag* står dagsformen när hälsodatan har något för i dag: *Ta det lugnt i dag* eller *Kör som planerat*, med skälen (`dayForm`). Dagen är trött vid en stark signal eller minst två svaga:

| Signal | Svag | Stark |
|---|---|---|
| HRV mot eget snitt de 28 nätterna före (minst 14 nätter) | under 90 % | under 80 % |
| Vilopulsen mot eget snitt de 28 dygnen före (minst 14) | 5 slag över | 8 slag över |
| Sömnen i natt | under 6 h | under 4,5 h |

Saknas en signal säger kortet det (*Räknas inte i dag: HRV.*) och räknar på resten. En Garmin-klocka skriver troligen inte HRV till Apple Hälsa eller Health Connect, och Garmins Training Readiness och Body Battery finns bara i Garmin Connect.

### Var hälsodatan kommer ifrån

- **iPhone, webbappen:** en webbsida kan inte läsa Apple Hälsa. En genväg i appen *Genvägar* kan det, och öppnar sedan appen med länken. Genvägen nedan är inte provad i en telefon ([PROV.md](../PROV.md#appen-i-telefonen)).
- **Android, APK:n:** appen läser Health Connect själv ([nedan](#health-connect-i-android-appen)). Ingen länk behövs.
- **iPhone, den byggda appen:** att läsa Apple Hälsa direkt kräver HealthKit i den byggda iOS-appen, och den når en iPhone bara genom TestFlight med ett utvecklarkonto hos Apple. Det väntar på kontot.

**Genvägen på iPhone** (*Genvägar* → *+*), med åtgärderna i ordning:

1. *Hitta hälsoexempel* där *Typ* är *Vilopuls* och *Startdatum* är *de senaste 30 dagarna*.
2. *Upprepa med varje* objekt i resultatet: *Text* med `{"date":"` *Startdatum* (formaterat *ISO 8601*) `","rhr":` *Värde* `}`, och *Lägg till i variabel* `dagar`.
3. Samma två steg för *Pulsvariabilitet* med `"hrv":` och för *Sömnanalys* med *Slutdatum*, `"sleep_h":` och *Varaktighet* i timmar. Välj bara sömnens stadier, inte *I sängen* eller *Vaken*.
4. Gärna *Hitta hälsoexempel* för *Träningspass* de senaste 14 dagarna, med `{"sport":"` *Typ* `","start":"` *Startdatum* `","minutes":` *Varaktighet* i minuter `}` i variabeln `pass`. Om *Träningspass* finns i listan beror på iOS-versionen.
5. *Text* med `{"kind":"troskel-health","format":1,"workouts":[` `pass` sammanfogat med `,` `],"days":[` `dagar` sammanfogat med `,` `]}`.
6. *Koda med Base64* (radbrytningar: *Inga*), och *Öppna URL:er* med `https://generaltgul.github.io/troskel/app/#h=` och resultatet.

Genvägen kan läggas på hemskärmen eller köras av en automation varje morgon. Hälsodatan passerar bara genvägen och appen i telefonen.

### Health Connect i Android-appen

APK:n och appen från Google Play läser Health Connect med pluginen [`@capgo/capacitor-health`](https://github.com/Cap-go/capacitor-health) (8.x, MPL-2.0). Den läser bara och skriver aldrig något. Det appen läser blir samma hälsodata som [länken](#länken-med-hälsodata) ger, så passen bockas av och dagsformen räknas på samma sätt.

- **Första gången:** *Läs från Health Connect* under *Om appen* visar Health Connects ruta för behörigheterna. Avsnittet säger först vad appen läser och varför, och länkar till [integritetspolicyn](https://generaltgul.github.io/troskel/app/privacy.html#halsodata). Saknas Health Connect (Android 13 och äldre utan appen från Play Butik), eller nekas behörigheten, säger appen det.
- **Sedan:** appen läser Health Connect varje gång den startar och när den kommer tillbaka till skärmen, utan att fråga (`checkAuthorization`). *Läs Health Connect nu* läser direkt, och *Öppna Health Connect* öppnar behörigheterna. *Ta bort all data på enheten* tar bort hälsodatan och slår av läsningen.
- **Vad den läser,** de senaste 35 dygnen (Health Connect ger högst 30 dygn före första godkännandet):

| Health Connect | Behörighet | Blir |
|---|---|---|
| Träningspass (`ExerciseSessionRecord`) | `READ_EXERCISE` | ett pass: idrotten med `sportOf`, starten i lokal tid och minuterna |
| Puls (`HeartRateRecord`) | `READ_HEART_RATE` | passets snitt- och maxpuls, bara för passens tid (en klocka skriver puls hela dygnet) |
| Sömn (`SleepSessionRecord`) | `READ_SLEEP` | sömnen för dagen sömnen slutade; stadierna sover, lätt, djup och REM räknas, och utan stadier hela sessionen |
| Vilopuls (`RestingHeartRateRecord`) | `READ_RESTING_HEART_RATE` | dagens vilopuls (snittet om det finns flera) |
| HRV (`HeartRateVariabilityRmssdRecord`) | `READ_HEART_RATE_VARIABILITY` | dagens HRV |

- **Garmin:** Garmin Connect skriver pass, puls och sömn till Health Connect, men inte HRV, så med en Garmin-klocka räknar dagsformen utan HRV (*Räknas inte i dag: HRV.*). Vilopulsen från Garmin är inte bekräftad.
- **Behörigheterna i `AndroidManifest.xml`:** pluginen tar med läs- och skrivbehörigheter för alla sina datatyper. Manifestet behåller de fem ovan och tar bort resten med `tools:node="remove"`, så att appen inte kan be om mer. Pluginens `PermissionsRationaleActivity` är sidan Health Connect visar när någon trycker på *Integritetspolicy* i behörighetsrutan; den visar `public/privacy.html` ur appen själv (`health_connect_privacy_policy_url` i `strings.xml`), så att den går att läsa utan nät.
- **Android 8 eller senare:** pluginen kräver API 26, så `minSdkVersion` är 26 (var 24).
- **iPhone:** pluginen kan också läsa HealthKit, men det kräver den byggda iOS-appen och ett utvecklarkonto, och är inte påslaget. Webbappen och iOS-appen läser alltså inte Health Connect eller HealthKit; `healthConnectPlugin` ger pluginen bara på Android.

Koden står i `app/src/model/healthconnect.ts` (`fromHealthConnect` gör om datan, `readHealthConnect` frågar pluginen) och proven i `app/test/healthconnect.test.tsx`, med en låtsasplugin. I en riktig Health Connect är det inte provat ([PROV.md](../PROV.md#appen-i-telefonen)). Google Play kräver dessutom en deklaration av hälsobehörigheterna i Play Console innan en version med dem får publiceras ([Butikerna](butikerna/butikerna.md)).

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
- De dagar som inte går stryks passen (loppen ligger kvar). Motorn flyttar dem inte till en annan dag, eftersom motorn i Python inte har dagar som faller bort. Appen minns dagarna efter omräkningen (`unavailable` i `app/src/model/storage.ts`): de står kvar i listan under *Mina tider*, så att nästa *Spara* stryker passen igen i stället för att lägga tillbaka dem, och ett eget schema som förnyar sig stryker passen de dagarna i de nya veckorna (`renewOwn`). Skickar coachen samma schema igen har det passen de dagarna, och dagarna väntar då med tiderna under *Mina tider* tills idrottaren trycker *Spara*. Ett annat schema börjar utan dem. Dagarna följer inte med i kopian eller till coachen när schemat redan är omräknat.
- Går det inte att göra ett schema, till exempel utan någon dag för schemats idrotter, sparas tiderna på enheten som förut, och appen säger att de används när coachen skickar ett nytt schema.

Samma regel står i Python som `rebuild` i `app/scripts/make_fixtures.py`, skriven med Python-motorns egna funktioner. Med flera idrotter ger appen varje pass samma idrott som Tröskel och skriver idrottens namn först i titeln ([Träningsplanen → Flera idrotter](../TRANINGSPLAN.md#flera-idrotter)). Målen i puls och fart räknas inte om i appen: de beror på testet, inte på tiderna, och följer med schemat från Tröskel.

## En kopia av schemat

Appen har inget moln, så schemat, ändringarna och det som är avbockat finns bara på enheten. Under *Om appen* finns ***En kopia av schemat*** (`app/src/views/Backup.tsx`, `app/src/model/backup.ts`, #790), när appen har ett eget schema eller ett från en coach (inte exemplet):

- **Kopian är det coachen får** ([Från idrottare till coach](#från-idrottare-till-coach)): samma `troskel.athlete`, med schemat som det ser ut i telefonen (ändrade pass, egna värden och tider), det som är avbockat, passen utanför schemat och tider som väntar under *Mina tider*. *Spara en kopia* sparar filen `troskel-kopia-<dag>.troskel`, och *Kopiera kopian som länk* kopierar länken `…#a=<data>`. Rutan kräver inget godkännande, eftersom kopian går till idrottaren själv. Hälsodatan (nätterna och klockans pass) följer inte med; klockan ger den igen. Det klockan bockat av följer med och blir idrottarens egna bockar.
- **Läs in kopian:** *Öppna en fil* eller *Klistra in en länk* på välkomstsidan (eller under *Ta emot ett nytt schema*), eller länken öppnad i telefonen. Appen känner igen kopian (`readCopyText`) och lägger tillbaka schemat, bockarna, passen utanför schemat och de väntande tiderna. Är telefonens schema ett annat (ett annat schema-id) frågar appen först. Bockarna i kopian går före telefonens för samma pass, och passen utanför schemat finns en gång var (`mergeLog`).
- **Appen säger till när lagringen kan tömmas** (`storageState`): i en flik i webbläsaren, och i en webbapp på hemskärmen som webbläsaren inte har lovat att spara (`navigator.storage.persist`). Appen ber om beständig lagring bara i en webbapp på hemskärmen, eftersom Firefox frågar användaren i en flik. Rutan i *Om appen* säger läget, och *I dag* påminner om en kopia när lagringen kan tömmas och den senaste kopian är äldre än 14 dagar (`settings.copied`). Appen i butikerna påminner inte.

**När lagringen töms**, enligt källorna (läst 2026-11-18):

- **Safari i en flik:** WebKit tar bort allt som en sida skrivit (`localStorage`, IndexedDB, service workerns cache) efter sju dagar av Safari-användning utan att sidan besökts ([WebKit, *Full Third-Party Cookie Blocking and More*](https://webkit.org/blog/10218/full-third-party-cookie-blocking-and-more/), 2020).
- **Webbappen på hemskärmen:** räknas inte som Safari och har sin egen räknare för användningsdagar, så sjudagarsregeln gäller inte (samma inlägg, tillägget om hemskärmen). Den har samma kvot som i webbläsaren, och vid platsbrist tar WebKit bort det som använts längst sedan, utom lagring som gjorts beständig. Beständig lagring ges efter heuristik, till exempel att sidan öppnas som webbapp på hemskärmen ([WebKit, *Updates to Storage Policy*](https://webkit.org/blog/14403/updates-to-storage-policy/), Safari 17).
- **Appen i butikerna:** Preferences töms inte av sig själv. Avinstalleras appen försvinner de. På Android har appen `android:allowBackup="true"`, så Googles automatiska säkerhetskopia (upp till 25 MB per app, i användarens Google Drive) läggs tillbaka när appen installeras igen, om säkerhetskopian är på i telefonen ([Android, *Back up user data with Auto Backup*](https://developer.android.com/identity/data/autobackup)). En APK från `android-test` installerad ovanpå en äldre behåller sina data.

Proven står i `app/test/backup.test.tsx`. Vägen från en telefon till en annan är provad med två rena Chromium-profiler (2026-11-18), inte i en riktig telefon ([PROV.md](../PROV.md#appen-i-telefonen)).

## Integritet

Appen skickar inga data någonstans. Det idrottaren själv delar med *Skicka schemat till din coach* ([Från idrottare till coach](#från-idrottare-till-coach)) går som en fil eller en länk som idrottaren ger vidare. Appen har ingen spårning, ingen analys och inga anrop till andra tjänster. Schemat, tiderna, det idrottaren bockat av, passen utanför planen, [hälsodatan](#hälsodatan) och inställningarna (språket och väggläget) sparas bara på enheten, och *Ta bort all data på enheten* tömmer lagringen.

Var de sparas står i `app/src/model/storage.ts`:

- **Webbversionen** (GitHub Pages och på hemskärmen) sparar i webbläsarens `localStorage`.
- **Appen i butikerna** sparar med Capacitor Preferences, som är telefonens egen lagring för appar (UserDefaults på iOS, SharedPreferences på Android). iOS kan tömma en webbvys `localStorage` när telefonen har ont om plats, men inte Preferences. När webbläsaren tömmer lagringen står i [En kopia av schemat](#en-kopia-av-schemat). `Capacitor.isNativePlatform()` väljer lagringen.
- Preferences är asynkron. Appen läser därför in allt en gång vid starten (`openStore` i `main.tsx`) och läser sedan ur minnet och skriver igenom till Preferences, i ordning. Vyerna är synkrona som förut. Går Preferences inte att läsa startar appen med `localStorage` i stället.
- En installerad app som sparat i `localStorage` förut flyttar sina data till Preferences första gången, när Preferences är tom, och tömmer sedan `localStorage`. Misslyckas flytten ligger de kvar där.
- *Ta bort all data på enheten* tömmer både Preferences och `localStorage`.

Proven står i `app/test/storage.test.ts`. I en riktig telefon är lagringen inte provad ([PROV.md](../PROV.md#appen-i-telefonen)).

## Appen utan butiken

Appen behöver ingen butik för att provas. På Android installeras APK:n från en fast länk, och på iPhone och iPad läggs webbappen på hemskärmen. Länken och QR-koden att skicka till någon som ska komma igång själv står i [Kom igång med appen](kom-igang/kom-igang.md).

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

Efter första besöket fungerar appen utan nät: service workern (`app/src/sw.js`) sparar alla appens filer. Bygget skriver in listan över filerna och en ny version varje gång något ändras. Sidan själv kommer ur cachen först, också med nät: på en svag uppkoppling (ett gym i en källare) väntade appen förut på en vit sida tills nätet gav upp, fast alla filer fanns sparade. En ny version hämtas i bakgrunden när appen öppnas, och syns nästa gång den öppnas. Apparna från butikerna och APK:n har ingen service worker (`app/src/model/offline.ts`): filerna ligger i appen själv, och en cache från den förra versionen skulle annars svara efter en uppdatering. En äldre installation som registrerat en får den borttagen.

Exempelschemat, provens fixturer och planens texter kommer från Python:

```bash
python app/scripts/make_fixtures.py
```

Det skriver `app/public/demo.troskel`, länken och kalenderfilerna i `app/test/fixtures/` och `app/src/i18n/plan.*.json`. Provet `tests/plan/test_link.py` stoppar när någon av dem inte stämmer med Python, till exempel efter en ändrad text i `plan.sv.json`.

Det skriver också `app/test/fixtures/engine.json.gz`: scheman som Python-motorn bygger för demolöparna (nybörjare, motionär och vältränad, 5 till 20 veckor, med och utan mål, olika tider, rullande scheman och felen), förnyelser av ett rullande schema och `rebuild` med tidsändringar. `app/test/engine.test.ts` jämför motorn i appen med varje fall i sin helhet. Ändras motorn i Python stoppar `tests/plan/test_link.py` tills fixturerna skrivs om, och sedan stoppar Vitest tills TypeScript-motorn följer med. Python-motorn räknar med flyttal, så TypeScript-motorn gör räkneoperationerna i samma ordning och avrundar som Python (`app/src/engine/py.ts`). Fixturerna skrivs med Python 3.12 eller senare, där `sum` lägger ihop flyttal med kompensation. Appens egna texter står i `app/src/i18n/ui.sv.json` och `ui.en.json`.
