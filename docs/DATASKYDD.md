# Dataskydd i Tröskel

Hur programmet skyddar kundernas data, både i videoanalysen och i allt annat som puls och mål.

Sidan beskriver hur programmet skyddar uppgifterna i dag, vad lagen kräver, vad som återstår i programmet och det som ska göras utanför programmet före första betalande kund.

**Kort svar:** Allt ligger lokalt på datorn. Det enda som lämnar den är mejlet med rapporten och FIT-filen, när coachen trycker på *Skicka till idrottaren* för en person som har samtyckt till kontakt. Vanlig e-post är inte krypterad, se [Mejl till idrottaren](#mejl-till-idrottaren). Filerna heter efter testets nummer, inte efter personen. Filmen tas bort 30 dagar efter testet. Raderingen tar bort allt programmet når och listar resten. Samtycket registreras per idrottare och styr vad programmet gör. Säkerhetskopian är krypterad, och programmet går att låsa med PIN-kod. Databasen och filerna på disken är inte krypterade: det är nästa steg (åtgärd 10), och fram till dess hänger skyddet av en stulen dator på Windows. Pappren i [avsnitt 4](#4-utanför-programmet-inför-första-kunden) behövs före första kunden.

<!-- toc -->
## Innehåll

- [1. Nuläget](#1-nuläget)
  - [Vad raderingen gör](#vad-raderingen-gör)
  - [Samtycket styr programmet](#samtycket-styr-programmet)
  - [Mejl till idrottaren](#mejl-till-idrottaren)
  - [Lagringstider](#lagringstider)
  - [Säkerhetskopia, återställning och flytt](#säkerhetskopia-återställning-och-flytt)
  - [Lås och kundläge](#lås-och-kundläge)
  - [Det som fanns från början](#det-som-fanns-från-början)
  - [Uppgraderingen av en befintlig datamapp](#uppgraderingen-av-en-befintlig-datamapp)
- [2. Vad lagen kräver](#2-vad-lagen-kräver)
  - [Två lägen](#två-lägen)
  - [Kraven när förordningen gäller](#kraven-när-förordningen-gäller)
- [3. Åtgärder i programmet](#3-åtgärder-i-programmet)
  - [Kryptering: alternativen](#kryptering-alternativen)
- [4. Utanför programmet, inför första kunden](#4-utanför-programmet-inför-första-kunden)
  - [Utkast: samtycke](#utkast-samtycke)
  - [Utkast: informationstext](#utkast-informationstext)
  - [Utkast: registerförteckning](#utkast-registerförteckning)
- [5. Inför webb, app och online coaching](#5-inför-webb-app-och-online-coaching)
  - [Öppen källkod](#öppen-källkod)
- [6. Besluten](#6-besluten)
<!-- /toc -->

## 1. Nuläget

All data ligger i en mapp: `%APPDATA%\Lopanalys` på Windows, eller den mapp coachen valt i *Inställningar*. Demoläget (`--demo`) har en egen mapp, `demo` i datamappen, med egen databas, egna filmer och egen logg. Programmet kan alltså visas för en kund utan att en riktig person syns.

| Uppgift | Var och i vilket format | Skydd i dag | Kvar att göra |
|---|---|---|---|
| Namn, e-postadress (frivillig), födelsedatum, kön, längd, vikt, maxpuls, vilopuls, fri anteckning, samtycke | `lopanalys.sqlite`, tabellen `athletes` | Windows-kontots filrättigheter, PIN-kod i programmet | Databasen är inte krypterad (åtgärd 10). |
| Tester: puls och laktat per steg, RPE, pulskurvan, trösklar och zoner, löpstegsmått | Samma fil | Som ovan. Påminnelse när en person inte testats på 24 månader | Som ovan. |
| Träningsschema: tillgänglig tid, mål, nivå, testets trösklar, passen och hälsodeklarationens tre rutor med datum | Samma fil, tabellerna `plans` och `plan_events`. Namnet sparas inte i schemat | Som ovan. Raderas och följer med i utdraget tillsammans med personen. PDF:en och kalenderfilen som lämnas ut registreras, så att raderingen hittar dem | Som ovan. PDF:en och kalenderfilen har namnet i sig. |
| Film från testet | `recordings\session-<testets nummer>-<datum-tid>.mp4` med tidsfilen `.mp4.json`. Med två kameror också `…-2.mp4` från den andra kameran | Neutralt filnamn. Spelas bara in med samtycke till film. Tas bort 30 dagar efter testet (förval). Den andra kamerans film följer den första: samma samtycke, lagringstid, radering och utdrag | Filen är inte krypterad. |
| Livebilden från telefonen eller en nätverkskamera | Går över wifi från telefonen till datorn (RTSP eller MJPEG) och sparas inte på vägen | Stannar i det lokala nätverket. Programmet ansluter bara till adressen coachen skrivit eller valt | Strömmen är inte krypterad: den som är på samma wifi kan se den. Använd inte ett öppet eller delat gästnät. |
| Adressen till telefonen eller nätverkskameran | `lopanalys.sqlite`, inställningen `camera.recent`. Ett lösenord i adressen sparas med den | Visas inte i kameralistan och skrivs inte i loggen | Lösenordet står i klartext i databasen. |
| Nyckelbilder (PNG med skelett och vinklar) | `keyframes\session-<nummer>\session-<nummer>-g<analys>_…png` | Neutralt filnamn. Tas bort med testet eller personen | Ansiktet syns (åtgärd 12). |
| Rapporter (PDF) | `reports\rapport-<idrottarens nummer>-….pdf` som förval | Neutralt filnamn i datamappen. Sökvägen sparas, så att raderingen hittar filen var den än ligger | En rapport som flyttats eller mejlats når programmet inte. |
| FIT-fil av ett test | `test-<testets nummer>-<datum>.fit` där coachen sparar den | Neutralt filnamn överallt, och inget i filen säger vem det är (inget namn, ingen födelsedag, ingen vikt). Sökvägen sparas, så att raderingen hittar filen | En fil som laddats upp till Strava eller Garmin Connect ligger hos den tjänsten, på idrottarens eget konto. |
| Mejl till idrottaren | Rapporten (PDF) och FIT-filen som bilagor, från coachens eget e-postkonto | Bara med rutan *Kontakt om uppföljning*, bara efter en fråga som visar adressen och bilagorna. Anslutningen till servern är krypterad. PDF:en som mejlas sparas inte på disken | **Vanlig e-post är inte krypterad** hela vägen, PDF:en är inte lösenordsskyddad, och ett skickat mejl når raderingen inte. Kopian i kontots *Skickat* tar coachen bort själv. |
| Säkerhetskopia | En fil `.lopbak` som coachen sparar: databasen och nyckelbilderna, inte filmerna | Krypterad med coachens lösenord (AES-256-GCM, nyckel ur scrypt) | Utan lösenordet går den inte att öppna. |
| Utdrag om en person | Zip som coachen sparar med *Exportera allt* | Registreras och tas bort med personen | Inte krypterad: den lämnas till personen. |
| Export till Excel (CSV) | Fil som coachen sparar, för alla eller en person | Varning före och efter, registreras | Inte krypterad. |
| Kopior som programmet gör själv | `lopanalys.sqlite.v<n>.bak` före uppgradering, `lopanalys.before-restore-<tid>.sqlite` vid återställning | Tas bort av sig själva efter 30 dagar | Under de 30 dagarna är de fullständiga kopior. |
| Logg | `logs\lopanalys.log`, en fil per dag | Sparas i 30 dagar. Nämner tester med nummer och filer med filnamn, aldrig personer, sökvägar, e-postadresser eller lösenord | Texten i ett oväntat fel kan i sällsynta fall innehålla en uppgift. |
| Inställningar | Tabellen `settings`: coachens namn och kontakt, senaste pulsband med adress, PIN-kodens hash, e-postkontot (server, användare, applösenord) | Som databasen. Applösenordet sparas krypterat med Windows dataskydd (DPAPI) och går bara att läsa av samma Windows-konto på samma dator, inte ur en säkerhetskopia på en annan dator | Ett pulsband som kunden tar med sig blir kvar som "senaste enhet". Den som är inloggad på Windows-kontot kan använda e-postkontot genom programmet. |
| Skärmen | TV-vyn visar förnamnet. Kundläget döljer idrottarlistan och historiken medan ett test pågår | PIN-kod för att öppna, lås efter vila | PIN-koden skyddar skärmen, inte filerna. |

Sökvägar i datamappen sparas relativt mappen. En flyttad datamapp och en säkerhetskopia som återställs på en annan dator hittar därför sina filer.

### Vad raderingen gör

*Radera* på idrottarens sida, och *Återkalla samtycke*, frågar först och tar sedan bort:

- personen och alla tester ur databasen,
- filmerna med sina tidsfiler och varje tests bildmapp, om de ligger i datamappen,
- rapporter, utdrag och exporter om personen vars sökväg är sparad, var de än ligger.

Databasen skriver över raderade rader (`secure_delete`) och packas om (`VACUUM`), så att raderna inte ligger kvar i filen. Efteråt visar programmet en lista över det som det inte når:

- säkerhetskopior och exporter av hela registret som gjorts medan personen fanns med,
- rapporter som inte ligger kvar där de sparades,
- film utanför datamappen (coachens egna filer),
- programmets egna kopior, med datumet då de tas bort,
- och alltid: utskrifter, mejlade rapporter, äldre säkerhetskopior och film på kamerans minneskort.

**Raderade nummer minns programmet, utan namn** (tabellen `erased`). Återställs en säkerhetskopia från före raderingen tas personen bort igen när databasen öppnas. En rad räknas som den raderade bara när både numret och tiden den skapades stämmer.

Ett enskilt test som tas bort raderas på samma sätt.

### Samtycket styr programmet

Idrottarens sida har avsnittet *Samtycke*: datumet på den underskrivna blanketten och fyra rutor.

| Ruta | Vad programmet gör |
|---|---|
| *Test och mätvärden* | Saknas den frågar programmet innan testet startar. Coachen kan fortsätta, till exempel när hen testar sig själv. |
| *Film under testet* | Saknas den spelas ingen film in. Livebilden visas och analyseras under testet men sparas inte, och det blir inga nyckelbilder. |
| *Spara för uppföljning* | Saknas den frågar programmet när rapporten har sparats eller skrivits ut, och raderar då allt om personen utom rapporten som just lämnades. |
| *Kontakt om uppföljning* | Saknas den mejlar programmet inget till personen: knappen *Skicka till idrottaren* är grå och säger varför. |

Inget antas för personer som fanns i programmet före samtycket: de står som *Samtycke saknas* tills coachen fyller i det. Demopersonerna har samtyckt till allt.

### Mejl till idrottaren

Testet går att spara som FIT-fil, och coachen kan fylla i idrottarens e-postadress och skicka rapporten och FIT-filen.

- **Vad som skickas:** rapporten som PDF och testets FIT-fil, med en kort hälsning undertecknad med coachens namn och företag. Från *Samlad rapport* skickas den samlade rapporten och en FIT-fil per test.
- **Till vem:** adressen i fältet *E-post* på idrottaren. Den är frivillig, finns med i utdraget och tas bort med personen.
- **När:** bara när coachen trycker på *Skicka till idrottaren*, bara om rutan *Kontakt om uppföljning* är ikryssad, och först efter en fråga som visar adressen och bilagorna. Samtycket och adressen läses ur databasen i det ögonblicket.
- **Hur:** från coachens eget SMTP-konto under *Inställningar → E-post*. Ett eget konto för verksamheten, till exempel en Gmail, med ett applösenord. Programmet loggar in över STARTTLS eller TLS och har inget läge utan kryptering. Saknar servern kryptering, eller går certifikatet inte att lita på, skickas varken lösenord eller mejl.
- **Vanlig e-post är inte krypterad** hela vägen till mottagaren: efter coachens server går mejlet mellan e-postleverantörerna på deras villkor, och det ligger sedan i mottagarens brevlåda. **PDF:en är inte lösenordsskyddad.** Därför krävs samtycket, och blanketten säger det rakt ut, se [utkastet](#utkast-samtycke).
- **Vad som sparas:** på testet noteras tiden och vad som följde med (rapporten, FIT-filen). Adressen står inte där och inte i loggen. PDF:en som mejlas görs i en tillfällig mapp som tas bort.
- **Vad raderingen inte når:** mejlet hos mottagaren och kopian i kontots mapp *Skickat*. Det står i listan efter en radering. Töm *Skickat* i kontot med jämna mellanrum.
- **E-postleverantören** (Google, när kontot är en Gmail) behandlar mejlen åt coachen. Om det gör leverantören till personuppgiftsbiträde som kräver ett avtal, och vad som gäller för ett vanligt Gmail-konto jämfört med Google Workspace, är *ej verifierat mot källa*. Ta reda på det före första betalande kund.

Det är provat mot en låtsad e-postserver i testsviten, inte med ett riktigt konto, se [PROV.md](PROV.md#fit-filen-och-mejlet).

### Lagringstider

- **Film:** *Inställningar → Film från testerna* har valen *Ta bort direkt efter analysen*, *Ta bort 30 dagar efter testet* (förval) och *Behåll*. Rensningen går när programmet startar. Nyckelbilderna, måtten och analysen sparas alltid. Film utanför datamappen rörs inte.
- **Testdata:** en idrottare som inte testats på 24 månader visas överst på sidan *Idrottare* med en påminnelse. Programmet raderar inget av sig självt.
- **Logg och programmets egna kopior:** 30 dagar.

### Säkerhetskopia, återställning och flytt

- *Säkerhetskopiera* frågar efter ett lösenord (minst åtta tecken) och sparar en krypterad fil. Ingen okrypterad kopia blir kvar bredvid.
- *Återställ* tar en `.lopbak` med lösenordet, eller en zip från en tidigare version. Det som fanns sparas som en kopia i 30 dagar.
- *Flytta mapp* kopierar allt till den nya mappen, kontrollerar kopian och stänger programmet. Den gamla mappen töms när programmet startar igen. Bara programmets egna filer tas bort ur den.

### Lås och kundläge

*Inställningar → Lås och kundläge*: en PIN-kod (minst fyra tecken) som krävs för att öppna programmet, lås efter vila (förval tio minuter, aldrig medan ett test pågår) och *Lås nu*. PIN-koden sparas som en saltad hash. En glömd kod tas bort genom att starta programmet en gång med `--reset-pin`. Det går, eftersom koden skyddar skärmen och inte filerna.

### Det som fanns från början

- **Allt lokalt.** Programmet gör två slags anrop mot internet: hämtningen av pose-modellen från Googles publika lagring, som bara skickar `User-Agent: Lopanalys` (i Windows-bygget är modellen inbakad, så anropet görs normalt aldrig), och mejlet till idrottaren när coachen skickar det, se [Mejl till idrottaren](#mejl-till-idrottaren). Ingen telemetri, ingen felrapportering till någon tjänst.
- **Enheterna stannar i rummet:** löpbandet och pulsbandet över Bluetooth, QZ på `127.0.0.1`, GoPro över USB-nätet. Ej kontrollerat: om Chromium-delen (Qt WebEngine) som visar rapporten gör egna bakgrundsanrop. Rapportmallen har inga externa länkar.
- **Tillfälliga filer städas:** rapportens HTML och utskriftens PDF ligger i en temp-mapp som tas bort. De blir kvar i `%TEMP%` om programmet kraschar mitt i.
- **Repot** släpper inte in data: `.gitignore` stoppar `*.sqlite`, film och `data/`. Installationen rör bara `C:\Lopanalys`, inte datamappen.

### Uppgraderingen av en befintlig datamapp

När den nya versionen startar första gången på en dator med data:

1. Databasen kopieras till `lopanalys.sqlite.v<gamla versionen>.bak` och uppgraderas till version 9 (tabeller för radering, samtycke, sport och träningsschema, kolumner för samtycket, e-postadressen och klockans VO2max; inget tas bort).
2. Filmer, tidsfiler, nyckelbilder och rapporter i datamappen som heter efter en person döps om, en fil i taget, och sökvägarna i databasen skrivs om. Jobbet tål att avbrytas och fortsätter vid nästa start. Gamla loggrader får de nya filnamnen.
3. Filmer äldre än 30 dagar tas bort.

Det provas i testsviten mot en databas skriven av föregående version (`tests/storage/fixtures/main_v3.sql`). På riktig hårdvara är det inte provat än, se [PROV.md](PROV.md).

## 2. Vad lagen kräver

**Det här är en orientering, inte juridisk rådgivning.** Det som är kontrollerat mot IMY:s webbplats 2026-11-14 (genom en sökning) har en länk. Resten är ur minnet och märkt *ej verifierat mot källa*. Läs IMY:s vägledning för företag innan första kunden: <https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/>.

### Två lägen

- **Coachen testar sig själv, familj och vänner utan betalning.** Dataskyddsförordningen gäller inte behandling som en privatperson gör som ett led i verksamhet av rent privat natur eller som rör hushållet (artikel 2.2 c). Undantaget förutsätter att det saknas koppling till yrkesmässig eller kommersiell verksamhet. [IMY: syfte och tillämpning](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/introduktion-till-gdpr/syfte-och-tillampningar/)
- **Coachen tar betalt.** Då är det kommersiell verksamhet och allt nedan gäller, från första kunden. IMY säger inte ordagrant "betalning", men kommersiell koppling tar behandlingen ut ur undantaget. Slutsatsen är en tolkning, inte ett citat. Var gränsen går för en vän som swishar för laktatstickorna är oklart: räkna med att förordningen gäller så snart tjänsten erbjuds utåt.

### Kraven när förordningen gäller

| Krav | Vad det betyder här | Källa |
|---|---|---|
| Hälsouppgifter är känsliga (artikel 9) | Uppgifter om fysisk hälsa som ger information om hälsotillstånd är känsliga, och det räcker att de avslöjar hälsa indirekt. Puls, laktat, trösklar, vikt och anteckningar om skador bör behandlas som hälsouppgifter. IMY nämner inte laktat eller puls uttryckligen. | IMY (definitionen), tolkningen *ej verifierad* |
| Film | En film där personen går att känna igen är en personuppgift. Om film och pose-analys räknas som biometrisk uppgift (som bara är känslig när den används för att entydigt identifiera någon) fick jag inget svar på. | *ej verifierat mot källa* |
| Rättslig grund och undantag från förbudet | Känsliga uppgifter får bara behandlas med ett undantag i artikel 9. Det som passar är **uttryckligt samtycke** (9.2 a). Samtycket ska gälla bestämda ändamål, gå att återkalla när som helst, och det ska vara lika lätt att återkalla som att ge. Det som gjorts innan förblir lagligt. | [IMY: förordningen i fulltext](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/introduktion-till-gdpr/dataskyddsforordningen-i-fulltext/) |
| Ändamål, minimering, lagringstid (artikel 5) | Säg i förväg vad uppgifterna ska användas till, samla inte mer än som behövs, och bestäm hur länge de sparas. "Tills vidare" är ingen lagringstid. | *ej verifierat mot källa* |
| Information till kunden (artikel 13) | Kunden ska få veta vem som ansvarar, vad som sparas, varför, hur länge, vem som får uppgifterna, och sina rättigheter, när uppgifterna samlas in. | *ej verifierat mot källa* |
| Utdrag, rättelse, radering (artiklarna 15–17) | En begäran ska besvaras inom en månad. Tiden kan förlängas med två månader om begäran är komplicerad, och då ska personen få veta det inom den första månaden. Radering är inte absolut (bokföringen får vara kvar). | [IMY: svarstid](https://www.imy.se/vanliga-fragor-och-svar/hur-lang-tid-har-foretag-eller-myndigheter-pa-sig-att-besvara-min-begaran-om-radering/) |
| Registerförteckning (artikel 30) | Undantaget för organisationer under 250 anställda gäller inte när känsliga uppgifter behandlas. Coachen behöver alltså en förteckning. Att det gäller en enskild näringsidkare står inte uttryckligen, det följer av ordalydelsen. | [IMY: föra register](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/fora-register-over-behandling/) |
| Säkerhet (artikel 32) | Lämpliga tekniska och organisatoriska åtgärder i förhållande till risken. Kryptering nämns som exempel. Förordningen kräver inte en viss teknik. | *ej verifierat mot källa* |
| Personuppgiftsincident (artikel 33) | Anmäls till IMY senast 72 timmar efter att coachen fått veta om den, om den inte är osannolik att medföra risk. En stulen okrypterad dator med kunddata är en incident. En stulen krypterad dator är troligen ingen anmälningspliktig incident, vilket är det starkaste skälet för diskkryptering. | [IMY: personuppgiftsincidenter](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/personuppgiftsincidenter/), sista meningen *ej verifierad* |
| Konsekvensbedömning (artikel 35) | Krävs vid sannolikt hög risk, till exempel hälsouppgifter i stor omfattning. En coach med ett fåtal kunder är inte automatiskt omfattad. Är man osäker rekommenderar IMY att göra en. | [IMY: konsekvensbedömning](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/konsekvensbedomningar-och-forhandssamrad/vem-maste-gora-en-konsekvensbedomning/) |
| Biträdesavtal (artikel 28) | Den som lagrar uppgifterna åt coachen (molnlagring, webbhotell, en app-tjänst) är personuppgiftsbiträde och ska ha ett avtal med instruktioner, sekretess, säkerhet och regler för underbiträden. I dag finns inget biträde, eftersom allt är lokalt. | [IMY: biträdesavtal](https://www.imy.se/verksamhet/dataskydd/det-har-galler-enligt-gdpr/personuppgiftsansvariga-och-personuppgiftsbitraden/personuppgiftsbitradesavtal/) |
| Barn | Tester på minderåriga kräver sannolikt vårdnadshavarens samtycke. | *ej verifierat mot källa* |
| Bokföring | Kvitton och fakturor sparas enligt bokföringslagen (sju år) och raderas inte på begäran. Håll därför testdata skild från fakturaunderlaget. | *ej verifierat mot källa* |

## 3. Åtgärder i programmet

| Nr | Åtgärd | Läge |
|---|---|---|
| 1 | Fullständig radering | Gjord. Se [Vad raderingen gör](#vad-raderingen-gör). |
| 2 | Neutrala filnamn | Gjord. Befintliga filer döps om vid första starten. |
| 3 | Lagringstid för film | Gjord. 30 dagar som förval. |
| 4 | Samtycke per idrottare | Gjord. Se [Samtycket styr programmet](#samtycket-styr-programmet). |
| 5 | Utdrag med en knapp | Gjord. *Exportera allt* på idrottarens sida. |
| 6 | Krypterad säkerhetskopia och export | Gjord. CSV-exporten är fortfarande okrypterad, med varning och val per person. |
| 7 | Ren logg | Gjord. |
| 8 | Lås och kundläge | Gjord. |
| 9 | Demodata i en egen datamapp | Gjord. Demopersoner som redan ligger i den riktiga databasen ligger kvar tills coachen raderar dem. |
| 10 | **Krypterad databas** | Återstår, efter åtgärd 1–9. Se alternativen nedan. |
| 11 | Lagringstid för testdata | Påminnelsen efter 24 månader är gjord. Arkivering i gränssnittet återstår. |
| 12 | **Ansiktssuddning i nyckelbilder och rapport** som val. Pose-modellen ger redan ansiktets punkter, så rutan går att lägga utan ny modell. Film med suddat ansikte är ett större jobb. | Återstår. |

Mindre saker som återstår:

- Klipp som GoPron spelar in på sitt eget minneskort (`record_clip`, bara från kommandoraden) tas inte bort från kortet.
- "Senaste pulsband" i inställningarna rensas inte när en person raderas.
- Utdraget och CSV-exporten är inte krypterade.
- Rapporten som mejlas är inte lösenordsskyddad, och mejlet ligger kvar i e-postkontots mapp *Skickat*.

### Kryptering: alternativen

| Alternativ | Skyddar mot | Skyddar inte mot | Arbete | Anmärkning |
|---|---|---|---|---|
| **BitLocker på hela disken** | Stulen eller kasserad dator, disk som tas ur | Någon vid en upplåst dator, skadlig kod, kopior som lämnar datorn | Inget i programmet | Kräver Windows Pro eller "enhetskryptering" på Home. Skyddar också film, bilder, logg och temp-filer, som inget annat alternativ gör lika enkelt. |
| **Krypterad mapp (EFS)** | Andra Windows-konton på samma dator | Samma som ovan | Inget i programmet | Bara Windows Pro. *ej verifierat* |
| **SQLCipher** | Databasfilen är oläslig utan nyckel | Film och bilder | M–L | Community-utgåvan har en BSD-liknande licens som bör gå att förena med både sluten och öppen källkod. Python-paketet (`sqlcipher3`) och Windows-bygget i PyInstaller behöver provas. *Licens och paket ej verifierade mot källa.* Alternativ: SQLite3 Multiple Ciphers. |
| **Nyckeln i Windows DPAPI** | Nyckeln ligger inte i klartext, den är bunden till Windows-kontot | Den som är inloggad på kontot | S | Bekvämt (inget lösenord), men då hänger skyddet på kontolösenordet. Med ett eget lösenord i programmet blir skyddet starkare och en glömd kod betyder förlorade data. |
| **Kryptera film och bilder i programmet** | Filerna i vila | – | L | OpenCV läser och skriver filer på disk, så filmen måste dekrypteras till en tillfällig fil före analysen. Dålig nytta mot arbete: BitLocker plus kort lagringstid ger mer. |

## 4. Utanför programmet, inför första kunden

- **Samtyckesblankett** på papper, underskriven före testet (utkast nedan). Originalet i en pärm, datumet in i programmet.
- **Informationstext** (utkast nedan), på samma papper eller på baksidan.
- **Registerförteckning** (utkast nedan), ett dokument som coachen håller aktuellt.
- **Rutin för radering:** begäran tas emot muntligt eller skriftligt, görs inom en månad: radera i programmet, radera PDF:er och exporter, och anteckna datum. Säkerhetskopior som innehåller personen byts ut vid nästa kopia och den gamla förstörs.
- **Rutin för incident:** stulen dator, borttappat USB-minne, mejl till fel person eller intrång. Skriv ner vad som hänt och när, bedöm risken, anmäl till IMY inom 72 timmar om risken inte är osannolik, och berätta för de berörda om risken är hög. E-tjänsten finns på imy.se.
- **Skydd av datorn:** utan BitLocker och kontolås gäller att en stulen eller borttappad dator är en personuppgiftsincident, eftersom databasen, filmerna och bilderna går att läsa från disken. Programmets PIN-kod hindrar bara den som sitter vid skärmen. Säkerhetskopian är krypterad och bör förvaras på ett ställe, till exempel ett USB-minne i ett låst skåp. Den som har fjärråtkomst till datorn och kan läsa datamappen ska stå i registerförteckningen.
- **Kunden får sina data med sig:** rapporten på papper eller som PDF direkt efter testet. Skickas den med e-post ska kunden ha bett om det, eftersom vanlig e-post inte är skyddad: programmet kräver rutan *Kontakt om uppföljning*, se [Mejl till idrottaren](#mejl-till-idrottaren).
- **E-postkontot:** ett eget konto för verksamheten med tvåstegsverifiering och applösenord. Det ska stå i registerförteckningen, och frågan om biträdesavtal med leverantören ska vara besvarad.
- **Minderåriga:** vårdnadshavaren skriver under.

### Utkast: samtycke

> **Samtycke till test och löpanalys**
>
> Jag genomför ett test hos [Namn, adress, e-post] ("coachen").
>
> Coachen sparar mitt namn, födelseår, längd och vikt, min e-postadress om jag lämnar den, samt testets resultat: puls, laktatvärden, hastigheter, trösklar, träningszoner och mått på löpsteget. Uppgifterna används för att ge mig en rapport och för att jämföra med senare tester. De lagras på coachens dator och lämnas inte till någon annan än mig.
>
> Jag samtycker till (kryssa):
>
> ☐ att mina uppgifter och testresultat behandlas för att genomföra testet och ta fram rapporten
> ☐ att jag filmas under testet och att filmen och bilder ur den används för analysen. Filmen raderas efter 30 dagar, bilderna i rapporten sparas
> ☐ att resultaten sparas i upp till 24 månader för uppföljning. Utan kryss raderas allt när jag fått rapporten
> ☐ att coachen får kontakta mig om uppföljning och skicka rapporten och testets träningsfil (FIT-fil) till min e-postadress. Jag vet att de skickas med vanlig e-post, som inte är krypterad, och att rapporten inte är lösenordsskyddad
>
> Jag kan när som helst ta tillbaka samtycket, få en kopia av mina uppgifter, få fel rättade eller få allt raderat. Det räcker att jag säger till eller skriver till [e-post]. Jag kan klaga hos Integritetsskyddsmyndigheten (imy.se).
>
> Ort och datum: ____________ Namn: ____________ Underskrift: ____________
> (För den som är under 18 år: vårdnadshavarens underskrift)

### Utkast: informationstext

> **Så hanteras dina uppgifter.** Ansvarig: [Namn, adress, e-post]. Vi sparar det som står i samtycket, med ditt samtycke som grund. Uppgifterna ligger på coachens dator, inte i någon molntjänst, och delas inte med någon. Har du bett om det skickas rapporten till din e-postadress med vanlig e-post, som inte är krypterad. Filmen raderas efter 30 dagar och övriga uppgifter senast 24 månader efter ditt senaste test, eller tidigare om du ber om det. Kvitto och faktura sparas enligt bokföringslagen. Du har rätt till utdrag, rättelse och radering och kan ta tillbaka ditt samtycke när du vill.

### Utkast: registerförteckning

| Fält | Innehåll |
|---|---|
| Personuppgiftsansvarig | [Namn, firma eller privatperson, adress, e-post] |
| Ändamål | Genomföra löpanalys, laktattest och maxpulstest, ge rapport och följa utveckling över tid |
| Kategorier av personer | Kunder (vuxna, och minderåriga med vårdnadshavares samtycke) |
| Kategorier av uppgifter | Namn, födelsedatum, kön, längd, vikt, e-postadress. Hälsouppgifter: puls, laktat, trösklar, zoner, anteckningar. Film och bilder |
| Rättslig grund | Uttryckligt samtycke (artikel 9.2 a och 6.1 a). Bokföringen: rättslig förpliktelse |
| Mottagare | Kunden själv, som får rapporten och FIT-filen med e-post om hen har samtyckt. E-postleverantören för kontot som mejlen skickas från [namn, och biträdesavtal om det krävs]. [Ändras om en molntjänst eller app tillkommer: ange biträdet] |
| Överföring utanför EU/EES | Nej |
| Lagringstid | Film 30 dagar. Testdata 24 månader efter senaste testet. Samtyckesblanketten så länge uppgifterna finns kvar, plus [tid] |
| Säkerhet | PIN-kod och lås efter vila i programmet, krypterad säkerhetskopia i låst förvaring, programmet utan nätanslutning utom mejlet till kunden (krypterad anslutning till e-postservern, okrypterad e-post därefter), neutrala filnamn, film raderas efter 30 dagar, radering i programmet. [Diskens kryptering]. [Vem som har fjärråtkomst till datamappen] |

## 5. Inför webb, app och online coaching

Här ändras bilden: uppgifterna lämnar rummet, och en leverantör blir personuppgiftsbiträde.

- **Minimera det som läggs ut.** Online behövs träningsplanen, zonerna (pulsgränser och farter) och meddelanden. Laktatvärden får finnas online. Film, nyckelbilder och pulskurvor behövs inte där och kan stanna på datorn. Zoner och laktat är hälsouppgifter, så kraven nedan gäller dem.
- **Lagring i EU/EES** hos en leverantör som skriver biträdesavtal. En leverantör utanför EU kräver en särskild grund för överföringen. *ej verifierat mot källa*
- **Inloggning:** ett konto per kund, inga delade länkar till planer, tvåstegsinloggning eller engångslänk till e-post, och coachens konto alltid med tvåstegsinloggning. Varje kund ser bara sitt.
- **Kryptering:** TLS hela vägen och krypterad lagring hos leverantören. Vill man längre krypteras innehållet innan det lämnar datorn, så att leverantören inte kan läsa det.
- **Rättigheterna ska fungera online också:** radering och utdrag ska nå molnet i samma handgrepp, och samtycket får en egen ruta för online-delen.
- **Konsekvensbedömning:** gör en enkel innan online-delen byggs. Den är inte säkert ett krav i den här skalan, men den är billig och IMY rekommenderar den vid tvekan.
- **Synk från klockor** (Garmin, Polar, Strava) betyder fler hälsouppgifter och fler parter. Ta det som en egen fråga.
- **Hälsodatan i appen** ([Appen → Hälsodatan](app/app.md#hälsodatan)): passen, sömnen, vilopulsen och HRV ur telefonens hälsodata är känsliga uppgifter. Appen läser dem bara lokalt: ur en länk som telefonen själv gör (på iPhone en genväg), eller i Android-appen direkt ur Health Connect, med bara läsbehörigheter för pass, puls, sömn, vilopuls och HRV och först när idrottaren trycker på *Läs från Health Connect* ([Appen → Health Connect](app/app.md#health-connect-i-android-appen)). Den sparar de senaste 42 dagarna på enheten och skickar dem inte vidare. Integritetspolicyn i appen (`app/public/privacy.html#halsodata`) säger samma sak, och Health Connect visar den som appens skäl för behörigheterna. Länkens data står efter `#` och når aldrig någon server. *Ta bort all data på enheten* tar bort den. Coachen ser den inte.

### Öppen källkod

Programmet är öppen källkod under AGPL-3.0-or-later. Det betyder för dataskyddet:


- **Varje coach blir själv personuppgiftsansvarig** för sina kunder. Den som skrivit programmet ansvarar inte för deras register så länge den inte driver en tjänst åt dem. Driver den en gemensam server blir den biträde åt varje coach, med avtal. *ej verifierat mot källa*
- **Programmet ska vara säkert i förvalet,** eftersom de flesta aldrig ändrar inställningar: kort lagringstid för film, neutrala filnamn, krypterad säkerhetskopia, samtyckesrutorna, varning om disken är okrypterad, och ingen telemetri.
- **Skicka med mallarna** (samtycke, information, registerförteckning) som utkast, med en tydlig rad om att de inte är juridisk rådgivning.
- **Koden får aldrig innehålla riktiga data:** testerna kör på syntetiska personer och film, `.gitignore` stoppar databaser, filmer och rapporter, och provet `tests/tools/test_no_private_data.py` stoppar namn, hemmanätets adresser och riktiga serienummer.
- **Licenserna:** krypteringsbiblioteket (Apache-2.0/BSD) och MediaPipe (Apache-2.0) går ihop med AGPL-3.0. Pose-modellens egen licens, i modellkortet, är *ej verifierad mot källa*.

## 6. Besluten

Beslutade 2026-11-14:

- **Åtgärd 1–9 görs före första betalande kund,** och krypterad databas (nr 10) senare.
- **Filmen raderas av sig själv 30 dagar efter testet.** Nyckelbilder och mått sparas.
- **Testdata sparas högst 24 månader efter kundens senaste test,** med en påminnelse i programmet i stället för automatisk radering.
- **BitLocker och kontolås krävs inte av programmet.** Följden står under [Skydd av datorn](#4-utanför-programmet-inför-första-kunden).
- **Laktatvärden får finnas online.** Det gäller [avsnitt 5](#5-inför-webb-app-och-online-coaching) och ändrar ingen kod nu.
- **Testet går att spara som FIT-fil, och rapporten och FIT-filen går att mejla till idrottaren.** Mejlen skickas från coachens eget konto, och PDF:en lösenordsskyddas inte. Inloggning mot Strava väntar. Se [Mejl till idrottaren](#mejl-till-idrottaren).

Val som gjordes i arbetet:

- **Utan registrerat samtycke till test frågar programmet men stoppar inte.** Annars skulle coachen behöva kryssa i en ruta för att testa sig själv.
- **Utan samtycke till film visas livebilden ändå,** men inget av den sparas.
- **Frågan om radering efter rapporten ställs bara när ett samtycke är registrerat** och rutan *Spara för uppföljning* är tom. Personer från före samtycket raderas aldrig på det sättet.
- **Rapporter och utdrag med sparad sökväg raderas också utanför datamappen.** Film utanför datamappen rörs aldrig.
- **Demomappen ligger i datamappen** (`demo`), inte bredvid den.
- **En glömd PIN-kod tas bort med `--reset-pin`.**
- **Mejl kräver rutan *Kontakt om uppföljning*,** i stället för en femte ruta i samtycket. Blankettens text för rutan nämner därför e-posten.
- **Meddelandet går att ändra innan det skickas,** och adressen visas alltid först.
- **Ett test som mejlats noterar tid och innehåll, inte adressen.**
- **FIT-filen har stoppen för blodprov som egna vilovarv,** så att stegens snittfart inte dras ner av pausen.
