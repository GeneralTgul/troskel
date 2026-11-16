# Prov som väntar på hårdvara

*Allt i programmet är provat mot simulatorer och låtsade enheter (testsviten). Det här är proven som kräver riktig hårdvara: gymdatorn (Windows), GoPro HERO8, en iPhone som kamera, löpbandet, cykeltrainern, SkiErgen, pulsbandet, TV:n och skrivaren. Skriv datum och utfall i kolumnen Läge när ett prov är gjort.*

<!-- toc -->
## Innehåll

- [Kameran](#kameran)
- [Löptekniken](#löptekniken)
- [Bikefit och stakteknik](#bikefit-och-stakteknik)
- [Löpbandet och pulsen](#löpbandet-och-pulsen)
- [Cykeltrainern](#cykeltrainern)
- [SkiErgen](#skiergen)
- [Testerna och databasen](#testerna-och-databasen)
- [Rapporten och utskriften](#rapporten-och-utskriften)
- [FIT-filen och mejlet](#fit-filen-och-mejlet)
- [Programmet](#programmet)
- [Appen i telefonen](#appen-i-telefonen)
<!-- /toc -->

## Kameran

| Del | Prov | Godkänt när | Läge |
|---|---|---|---|
| GoPro hittas | HERO8 med USB-anslutning *GoPro Connect* i gymdatorn, `python -m lopanalys.camera list` | GoPron står först med modell och serienummer | |
| GoPro-info | `python -m lopanalys.camera gopro-info -v` | Modell, serienummer och programvara visas; webbkamerans status visas eller UNKNOWN | |
| GoPro live | `python -m lopanalys.camera preview gopro --seconds 30` | 1080p, cirka 30 bilder/s, fördröjning under 300 ms | Delvis 2026-11-13: bilden kom i programmet på gymdatorn (HERO8 över USB), men med en rejäl fördröjning. Rättningen (en avkodningstråd, liten buffert) är inte provad |
| Brandväggen | Första starten av webbkameran på Windows | Windows frågar om UDP 8554, eller bilden kommer direkt; annars visas felet om brandväggen | |
| GoPro återansluter | Dra ur USB-kabeln under live, sätt i den igen | Bilden kommer tillbaka av sig själv inom cirka 10 s | |
| Precist klipp | `python -m lopanalys.camera gopro-record --seconds 5 --fps 120` | Filen hämtas, är 1080p i 120 bilder/s med linsen Linjär, inga varningar om inställningar | |
| Klipp med analys | `python -m lopanalys.camera gopro-record --seconds 30 --fps 120 --analyze --height <cm>`, löpare på bandet från sidan | Filen hämtas och analyseras: raden visar cirka 120 fps, och inga värden står som *uncertain* | |
| HERO8 lämnar webbkameraläget | Precist klipp direkt efter live | Inspelningen startar; annars visas "spelar in" eller "upptagen" | |
| Hämtning | `gopro-download` av ett klipp på mer än 1 GB | Hela filen hämtas, ingen .part blir kvar | |
| Fel läge | USB-anslutning på MTP | Programmet visar texten om GoPro Connect | Se raden *Drivrutinen* nedan: i GoPro Connect-läget fanns kameran, men som seriell port |
| Webbkamera | En USB-webbkamera, `list --probe` och `preview 0` | Namnet syns, högsta bildfrekvensen väljs, uppmätt fps stämmer | |
| Inspelning | `preview 0 --record x.mp4 --seconds 20` | Filen spelas i Windows (H.264 om det finns, annars mp4v), med sidofil | |
| HERO9–13 | Om en sådan kamera finns: samma prov | Open GoPro-vägen fungerar | |
| Drivrutinen | HERO8 i GoPro Connect-läge på en Windows 11-dator utan GoPros program | Kameran blir ett nätverkskort (*GoPro RNDIS Device*) och datorn får en adress i 172.2x-nätet | 2026-11-13: Windows valde drivrutinen för seriella portar (*USB Serial Device*, COM3), och *Remote NDIS* gick inte att välja för hand. GoPro Webcam-programmet installerade rätt drivrutin. Programmet ska avslutas efteråt, annars håller det kameran. Kvar: programmet ska känna igen läget och säga vad coachen ska göra |
| Värmen | Livebild från HERO8 i 40 min | Kameran stänger inte av sig av värme; annars provas utan batteri | |
| Telefonen med adress | iPhone med *IP Camera Lite* på samma wifi som gymdatorn. *Lägg till telefon*, skriv telefonens IP-adress, *Prova adressen*. Från terminalen: `python -m lopanalys.camera find <adress>` | Minst en ström listas (MJPEG eller RTSP), och förhandsvisningen visar bilden. Skriv här vilken app, port och sökväg som gällde, och rätta listan `PHONE_APPS` om den inte stämde | |
| Telefonen på nätverket | *Sök på nätverket* med appen igång. Från terminalen: `python -m lopanalys.camera find` | Telefonen listas, eller så står det här att appen inte annonserar sig (då gäller adressen) | |
| Telefonens bildtakt | `python -m lopanalys.camera preview <adress> --seconds 60`, en gång med MJPEG-adressen och en gång med RTSP-adressen | Raden `stream …` visar minst 25 bilder/s, intervallets 95:e percentil under 80 ms, inga återanslutningar. Skriv här vilken av de två som var bäst | |
| Telefonens fördröjning | Rikta telefonen mot ett stoppur på datorns skärm med förhandsvisningen bredvid, och fotografera skärmen | Skillnaden mellan stoppuret och stoppuret i bilden är under 300 ms. Skriv värdet här för MJPEG och för RTSP | |
| Telefonen tappar wifi | Slå av wifi på telefonen i 10 s under en förhandsvisning och under ett test med två kameror | Bilden kommer tillbaka av sig själv. Under testet står *Bilden försvann* i den andra bilden, och den första kameran och testet fortsätter | |
| Telefonen med inloggning | En app med användarnamn och lösenord, adressen skriven som `användare:lösenord@adress` | Bilden kommer. Utan inloggningen säger programmet hur adressen ska skrivas. Lösenordet står inte i kameralistan eller i loggen | |
| Telefonen i 40 min | Livebild från telefonen under en hel laktattrappa, laddaren i | Appen fortsätter sända, telefonen somnar inte och blir inte för varm | |

## Löptekniken

| Del | Prov | Godkänt när | Läge |
|---|---|---|---|
| Pose-modellen | Starta programmet på gymdatorn utan internet (inbakad modell), och en gång med internet utan inbakad modell | Modellen hittas respektive laddas ner, kontrollsumman stämmer, ingen felruta | Delvis 2026-11-13: den inbakade modellen hittas på gymdatorn (egenkontrollen och livebilden). Nedladdningen är inte provad |
| Pose live | Livevyn med GoPro över USB, löpare på bandet från sidan | Skelettet följer löparen, vinklarna syns från 3 m, analysen ligger inte efter kameran | Delvis 2026-11-13: skelettet ritades på löparen i livebilden. Kvar: på bandet från sidan, och takten i loggen |
| Steghändelser | Cirka 10 min i tre farter, filmat från sidan i 120 bilder/s på GoProns kort, med klockan och HRM-Pro+. Poserna med `tools/granska_analys.py FILM --height <cm>`, sedan `tools/jamfor_garmin.py <ut>/poses.pkl --height <cm> --fit <klockans fil> --video FILM` | I 120 bilder/s: kadensen ±2 steg/min, kontakttiden ±15 ms och studsen ±1 cm mot bandet, per halvminut | |
| Bildtakten | Samma körning: `jamfor_garmin.py` analyserar också var 2:a, 4:e och 5:e bild (60, 30 och 24 bilder/s) | Tabellen *lowest tested rate that holds* ger kraven per mått. De skrivs in i `pose/reliability.py`, och bildarken i 30 bilder/s (`granska_analys.py --every 4`) granskas mot 120 | Kraven är provisoriska, satta 2026-11-14 ur den syntetiska löparen: knäböjningen vid isättningen höll inte i 30 bilder/s (+3 grader mot 120) och underbenets vinkel inte i 24 |
| Kadensen ur inspelningen | Löpteknik med GoPron över USB (webbkameraläget, cirka 25 bilder/s), analysera inspelningen efteråt | Kadensen i resultatet stämmer med Zwift och pulsbandet (±3 steg/min). Före rättningen 2026-11-14: 233 steg/min, eftersom analysen räknade med filens nominella 30 bilder/s | Fel 2026-11-14: 233 steg/min. Rättningen (sidofilens tider) är inte provad på gymdatorn |
| Videoanalys | Analysera en GoPro-fil på 60 s (120 bilder/s) | Klar utan minnesproblem, 4–8 nyckelbilder med rätt bild och vinklar, fynden rimliga för löparen | |
| Bakifrån | Spela in bakifrån och kontrollera höftfall, knä inåt och stegbredd | Värdena rimliga, och höger och vänster rätt | |
| Två kameror live | Löpteknik med GoPron från sidan och telefonen bakifrån, minst 5 min | Båda bilderna har skelett på TV:n och på operatörssidan, och båda raderna med rutor får värden. Loggens rader `two views: …` visar att den första vyn analyserar minst 80 % av kamerans bildtakt. Skriv bildtakterna och den andra vyns tak här | |
| Två kameror: processorn | Samma test med Aktivitetshanteraren öppen | Programmet lämnar processor över, fläkten varvar inte, och fördröjningen i den första bilden är som med en kamera | |
| Två kameror: stegen | Resultatet efter testet ovan, fliken *Löpteknik* | Båda filmerna är analyserade, var och en med sina fynd. Raden under den andra kameran säger att minst 90 % av stegen hittades i båda filmerna, och eftersläpningen stämmer med provet *Telefonens fördröjning* minus GoProns fördröjning | |
| Två kameror: rapporten | Skriv ut rapporten från testet ovan | Bilderna, mätvärdena och fynden finns en gång per kamera med hållet i rubriken, och första sidan har det viktigaste fyndet från båda | |
| Två webbkameror | Två USB-webbkameror, eller GoPron och en webbkamera | Båda går att välja, och Windows klarar båda samtidigt (två kameror på samma USB-hubb kan slå i taket för USB) | |
| Nyckelbilderna | Skriv ut rapporten | Linjer, text och ringar skarpa på papper; bilderna ljusa nog att se löparen; lägesfärgerna under *Mätvärden* lika kraftiga som på sidan *Det vi såg* | Fel 2026-11-14: bilderna nästan svarta och lägesfärgerna bleka på papper. Rättat (ljusare kopia av bilderna i rapporten, fulla färger i staplarna), inte utskrivet |

## Bikefit och stakteknik

Analysen är byggd och provad mot syntetiska cyklister och stakare, där sanningen är känd. Ingenting är provat på en riktig person, och riktvärdena låses inte förrän proven nedan är gjorda. Vad som mäts står i [handboken](handbok/bikefit/bikefit.md) och i [underlaget](sporter/bikefit-och-stakteknik.md).

| Del | Prov | Godkänt när | Läge |
|---|---|---|---|
| MediaPipe på en cyklist | Filma en cyklist på trainern från sidan (GoPro *Linear*, 3–4 m, 60 och 120 bilder/s) och kör `python tools/granska_analys.py FILM --sport bike --height 180` | Bladen `bottoms.png` och `tops.png` visar att höft, knä och fotled sitter rätt i varje bild, och att den inramade bilden är nedersta respektive översta läget | |
| Knävinkeln mot goniometer | Mät knävinkeln med goniometer med pedalen i klockan 6 (statiskt), och jämför med programmets *Knäflexion i nedersta läget* | Programmets värde ligger 5–11° över det statiska (video under tramp ger cirka 8° mer). Skriv båda talen här. Avviker det mer ska mätfelet ±5° och intervallet 33–43° ses över | |
| Sadelhöjden i mm | Höj sadeln 5 mm och 10 mm och filma om | Knävinkeln sjunker med ungefär det programmet räknar med (cirka 2,5 mm per grad för 178 cm). Skriv hur mycket den sjönk: fotleden tar upp en del | |
| Kadensen | Jämför *Kadens* med trainerns kadens under filmen | ±2 varv/min | |
| Händerna på styret | Samma film: syns armbågen och handleden? | Armens värden visas. Annars kommer varningen att styrstammen inte kan bedömas, och inget gissas | |
| Höftens höjdrörelse | Filma med rätt sadelhöjd och med sadeln 2 cm för högt | Värdet stiger tydligt med den höga sadeln. Annars ska värdet visas utan bedömning | |
| Framifrån | Filma framifrån i knähöjd, en gång normalt och en gång med knäna medvetet inåt | *Knäts sidorörelse* skiljer de två filmerna, och rätt knä pekas ut | |
| Bildfrekvensen | Samma cyklist i 30, 60 och 120 bilder/s | Knävinkeln nederst skiljer högst 3° mellan 60 och 120. Vid 30 märks mätningen som osäker | |
| Cykeln skymmer | Titta på nyckelbilderna: skymmer ramen eller veven foten? | Fotledens bana är en cirkel (ingen varning om cirkeln) | |
| MediaPipe på SkiErgen | Filma från sidan (stående bild, 60 bilder/s) och kör `python tools/granska_analys.py FILM --sport ski --height 180` | Bladen `starts.png` och `finishes.png` visar rätt bild för dragets start och slut, och axel, armbåge och handled sitter rätt även i högsta läget | |
| Dragtiden mot PM5 | Jämför *Dragtid*, *Återgång* och *Takt* med det PM5 visar | Takten ±1 drag/min, dragtiden ±50 ms. Skriv skillnaden här: PM5 mäter kraften i kedjan och programmet handens rörelse | |
| Stolpen skymmer | Titta på nyckelbilderna i slutläget | Handleden närmast kameran syns hela draget | |
| Stakteknikens riktvärden | Filma en ovan och en van skidåkare | Den vana åkarens värden ligger inom intervallen. Annars ändras erfarenhetsvärdena efter det | |
| Jämförelsen | Två tester med några veckor emellan, samma plats för fötterna | Värden som inte ändrats visas som *Oförändrat* | |
| Rapporten | Skriv ut en bikefit med ifylld checklista och ett staktest | Bilderna skarpa, tabellen ryms på sidan och *Förslag på cykeln* går att läsa som en arbetsorder | |
| Testtypen i guiden | Kör *Bikefit* på gymdatorn med GoPron från sidan (120 bilder/s) och telefonen framifrån, och *Stakteknik* med GoPron | Filmen börjar när uppvärmningen är slut, har lika många bilder per sekund som kameran ger, analyseras efter testet och fliken öppnas av sig själv. Båda filmerna av en bikefit får en del var på fliken | Byggt 2026-11-14 och provat i demoläget med de syntetiska idrottarna, inte med en kamera |
| Vinklarna live på TV:n | Bikefit med GoPron från sidan och telefonen framifrån, och stakteknik med GoPron. Höj sadeln 5 mm under uppvärmningen | Rutorna under bilden får värden inom 10 s, *Knäflexion nere* minskar efter höjningen, och värdena i slutet av det filmade steget ligger inom 3° från resultatet efter testet | Byggt 2026-11-15 och provat i demoläget med de syntetiska idrottarna, inte med en kamera |
| 120 bilder/s i inspelningen | Bikefit med GoPron i 120 bilder/s: jämför antalet bilder i filmen med filmens längd | Minst 110 bilder per sekund i filmen. Inspelningen läser kamerans senaste bild 240 gånger i sekunden, och en dator som inte hinner tappar bilder | |

## Löpbandet och pulsen

| Del | Prov | Godkänt när | Läge |
|---|---|---|---|
| Bandet över FTMS | Stäng QZ, skanna och anslut SW1000EAI-0001 från gymdatorn | Bandet syns i listan och visar ansluten | |
| Bandet över FTMS | Tryck START på bandet, sätt 8 → 12 km/h och 0 → 2 % | Bandet går till farten och lutningen, livevärdena stämmer med panelen | |
| Bandet över FTMS | Stå still 10 min ansluten | Bandet somnar inte | |
| Bandet över FTMS | Dra ur säkerhetsnyckeln under gång | Programmet visar att bandet stannat | |
| Bandet över FTMS | Slå av bandet och på igen under ett test | Programmet ansluter igen och skickar farten på nytt | |
| Bandet över FTMS | Försök ansluta med QZ igång | Programmet visar "Stäng QZ först, bandet tar bara en anslutning åt gången." | |
| Pulsbandet | Anslut HRM-Pro+ | Puls och RR syns, batteriet visas | |
| Pulsbandet | Ta av bandet en stund | Meddelandet om hudkontakt syns, inga nollvärden | |
| Pulsbandet: kadensen (RSC) | Anslut HRM-Pro+ och spring 2 min på 10 och 13 km/h med Zwift igång. Att bandet sänder löpdata över Bluetooth med tjänsten RSC (0x1814, mätvärdet 0x2A53) är Garmins uppgift och inte kontrollerat mot källa | Loggen säger *running cadence (RSC) available*; rutan heter *Kadens (pulsbandet)* och visar samma steg/min som Zwift (±2), inte halva (en fot); resultatet och rapporten säger *Kadens (pulsbandet)*. Utan RSC: rutan heter *Kadens* och kamerans värde visas | 2026-11-14: RSC finns på HRM-Pro+ (loggen), men kadensen visade 84 vid 10 km/h: bandet sänder varv (en fot). Fördubblas sedan dess under 125. Kvar: jämför med Zwift |
| QZ | QZ igång med bandet anslutet, välj läget QZ | Porten läses ur registret, fart, lutning och puls syns live | |
| QZ | Sätt 10 km/h och 1 % från programmet, stoppa | Bandet följer; stopp pausar QZ utan att avsluta QZ:s pass | |
| QZ | Kontrollera att sträckan från QZ är i km | Sträckan i programmet stämmer med QZ | |

## Cykeltrainern

*Koden för cykel är skriven mot simulatorer och en låtsad Bluetooth-klient. Inget här har mött en riktig trainer. Byteformaten för Indoor Bike Data och kontrollpunkten är skrivna ur minnet av Bluetooth SIG:s FTMS-specifikation, och att Tacx NEO 2T har FTMS är belagt i andrahand (recensioner), inte ur Tacx egen dokumentation.*

| Del | Prov | Godkänt när | Läge |
|---|---|---|---|
| Trainern hittas | Stäng Zwift och QZ. *Nytt test*, sport *Cykel*, steget *Trainer och puls*, *Sök* | Tacx NEO 2T står i listan. Den står inte i listan över löpband när sporten är *Löpning* | |
| Trainerns funktioner | Anslut och läs loggen | Raderna `features …` och `power range …` visar att ERG finns och ett effektintervall (till exempel 0–2200 W) | |
| Mätdata | Trampa 2 min med *Bluetooth (FTMS)* | Effekt och kadens på TV:n stämmer med Zwift eller en Garmin inom ±2 % respektive ±2 varv/min | |
| ERG | Laktattrappa med steg 150, 175 och 200 W vid kadens 85 | Trainern når målet inom cirka 5 s och håller det inom ±5 W i medel över 30 s. Loggen visar `control point power -> 0x01` | |
| Start före första målet | Första steget efter anslutning | Motståndet kommer utan att något görs på trainern. Skriv här om trainern kräver *Start* (0x07) före *Set Target Power* eller inte | |
| ERG vid låg kadens | Sänk kadensen under 50 mitt i ett steg i 15 s | TV:n säger *Öka kadensen*, testet fortsätter | |
| Fritt steg | *FTP 20 minuter*, 20-minuterssteget | Trainern släpper effektmålet och känns som platt väg (simulering 0 %). Effekten följer växlar och kadens | |
| Stopp | Avsluta ett test | Motståndet släpper, trainern går att trampa lätt | |
| Ramptestet avslutar sig | Sluta trampa mitt i ett steg i ramptestet | Efter cirka 5 s står *Du slutade trampa*, trainern går till 80 W och pulsen mäts en minut | |
| Trampvila i laktattrappan | Sluta trampa 10 s i ett laktatsteg | Testet fortsätter | |
| Två anslutningar | Försök ansluta med Zwift igång | Skriv vad som händer. Programmet ska säga att Zwift och QZ ska stängas, eller så fungerar båda (NEO 2T tar två Bluetooth-anslutningar) | |
| QZ igång | Försök ansluta trainern medan QZ kör | Programmet vägrar med *Stäng Zwift, QZ och andra träningsappar först* | |
| Återanslutning | Dra trainerns sladd under ett steg och sätt i den | Programmet ansluter igen, skickar *Start* och måleffekten på nytt | |
| Laktattrappa cykel | Hela testet med ERG och pulsband | Effekten byts vid varje steg, provet tas utan paus, medeleffekten per steg i resultatet stämmer med målet inom ±3 W | |
| FTP 20 minuter | Hela testet | FTP i resultatet är 95 % av medeleffekten trainern själv visar för de 20 minuterna | |
| W/kg | Idrottare utan vikt | Guiden frågar efter vikten efter steget *Testtyp*, och W/kg står i resultat och rapport | |

## SkiErgen

*Koden för Concept2 PM5 är skriven mot en simulator och en låtsad Bluetooth-klient. Passet som läggs in på monitorn använder CSAFE: ramens flaggor, stuffing och checksumma är kontrollerade mot öppen kod (droogmic/Py3Row), men kommandona för passet, enheterna och byteordningen är skrivna ur minnet, se tabellen i [enheter.md](sporter/enheter.md#3-concept2-pm5-över-bluetooth).*

* UUID:erna för tjänsterna och karaktäristikerna 0x0031–0x003D är kontrollerade mot `pm5-ble.js` i ergarcade/pm5-base. **Fältens ordning och storlek i varje paket är skrivna ur minnet** av Concept2:s specifikation och är inte kontrollerade mot den. Första provet nedan avgör om tolkarna stämmer.*

| Del | Prov | Godkänt när | Läge |
|---|---|---|---|
| PM5 hittas | Väck monitorn. *Nytt test*, sport *SkiErg*, *Sök* | Monitorn står i listan med namn (PM5 och serienummer). Räckvidden från gymdatorn räcker | |
| Fälten stämmer | Staka 2 min (*Just Ski*, eller i ett inlagt pass) och jämför med monitorns skärm | Watt, tempo per 500 m och takt på TV:n är samma som på monitorn. Stämmer de inte: rätta byte-lägena i `devices/pm5.py` mot Concept2:s specifikation | |
| Drag för drag | Staka 20 drag och räkna | Loggen eller en lyssnare på ämnet `stroke` visar 20 drag med dragtid och återgångstid som är rimliga (cirka 0,6 s och 0,9 s) | |
| Passet på monitorn | Starta en laktattrappa med monitorn ansluten | Operatörssidan säger *Testet är inlagt som pass på monitorn*, och monitorn visar ett intervallpass som väntar på första draget. Loggen har raden `workout of … intervals programmed`. Står det `refused`: raden före säger vilken ram monitorn inte tog (`frame 3 of 8 not taken`), skriv den här | |
| Passets tal | Läs intervallen på monitorn före start | Längden (4:00), vilan (0:30) och måltempot per steg stämmer med protokollet. Är talen orimliga (till exempel 256 gånger för stora): vänd `PM_BYTE_ORDER` i `devices/csafe.py`. Skriv här vilken ordning som gällde | |
| Passet och TV:n i takt | Kör tre steg och jämför monitorns klocka med TV:ns | De skiljer högst ett par sekunder genom hela testet, också efter pauserna | |
| Passet utan vila | Laktattrappa med *Paus för laktatprov* 0 s, och ett ramptest | Monitorn tar emot intervall med vila 0 s och 30 intervall. Annars: skriv vad den svarade | |
| Passet nekas | Starta ett test medan monitorn står mitt i ett annat pass | Beskedet *Passet gick inte att lägga in på monitorn* visas, och testet går vidare med mätvärden som vanligt | |
| Stopp syns | Sluta staka | Effekten går till 0 inom cirka 6 s | |
| ErgZone samtidigt | Anslut ErgZone på telefonen först och sedan programmet, och tvärtom | Skriv vad som händer. Går det inte ska programmet säga att ErgZone och ErgData ska stängas | |
| Korta paket | Kör 20 min och läs loggen | Raden `short notifications` visar hur många stympade paket som kom, och inget värde på TV:n hoppar | |
| Pulsen | HRM-Pro+ ansluten till både monitorn och programmet | Pulsen syns på båda | |
| Återanslutning | Gå utom räckhåll och tillbaka under ett steg | Programmet ansluter igen och värdena kommer tillbaka | |
| Laktattrappa SkiErg | Hela testet | TV:n visar målet och utfallet, pausen på 30 s räcker för provet, och stegets effekt i resultatet är den uppmätta med målet inom parentes | |
| Utanför målet | Håll 10 % under målet i ett steg | Rutorna blir gula på TV:n, och steget är gult i resultatet | |
| Ramptestet avslutar sig | Sluta staka mitt i rampen | Efter cirka 15 s står *Du slutade staka*, och pulsen mäts en minut | |
| Dragfaktorn | Ställ spjället, läs dragfaktorn på monitorn och kör en laktattrappa | Resultatet visar samma dragfaktor under tabellen *Steg* med *Läst från monitorn*, och rapporten skriver den. Står rutan tom läser `devices/pm5.py` fel byte i paketet 0x0031 | |
| Medeleffekt för hand | Stäng av monitorns Bluetooth mitt i ett steg, rätta steget i resultatet med monitorns medeleffekt | Steget visar talet med * och målet, trösklarna räknas om, och rapporten har *-noten | |
| TV:n på avstånd | Stå vid SkiErgen | Målet, watten och tempot går att läsa från åkarens plats | |

## Testerna och databasen

| Del | Prov | Godkänt när | Läge |
|---|---|---|---|
| Laktattrappa med FTMS-band | Kör *Laktattrappa, motionär* på bandet med pulsband | Bandet byter fart vid varje steg, stannar i 30 s vid provet och startar igen; pulsen per steg stämmer med klockan | |
| Manuellt läge | Kör ett test med manuellt band | TV:n visar "Sätt bandet på …" och förvarningen 15 s före; steget börjar när operatören bekräftar | |
| Maxpulstrappa | Kör lutningstrappan till utmattning, tryck *Utmattad* | Bandet går ner till 5 km/h och stannar efter en minut; maxpulsen och återhämtningen efter 1 min stämmer med Garmin | |
| Bandet stannar efter testet | Kör ett test med FTMS-band till sista steget | Bandet går i 5 km/h, TV:n och operatörssidan räknar ned *Bandet stannar om …*, och efter 60 s står bandet stilla | 2026-11-15: bandet stod kvar på 5 km/h efter testet |
| Farten för hand | Höj farten och lutningen på bandets panel mitt i ett steg | Loggen säger *Bandet ställdes om för hand …*, steget sparas med bandets fart och lutning, och vid nästa steg säger TV:n vad som byttes | 2026-11-15: 18 km/h och 2 % för hand sparades som 17,5 km/h och 1 % |
| Kadensen på TV:n | Stå på bandet med pulsbandet några minuter innan du startar, kör sedan ett test | *Kadens (pulsbandet)* visar 150–190 från första minuten. Loggen har en rad i minuten, `cadence tile tv …` och `cadence tile operator …`, med samma värde som de sparade proverna | 2026-11-15: rutan visade ungefär 100 de första 6 minuterna. Trolig orsak rättad: kadensen före start räknades som de senaste 12 s |
| Databasen på Windows | Spara idrottare och test, *Säkerhetskopiera* med lösenord, *Återställ* | Allt finns kvar, nyckelbilderna syns; fel lösenord ger ett besked och ändrar inget; databasen ligger i `%APPDATA%\Lopanalys` | |
| Uppgraderingen av datamappen | Första starten av den nya versionen på gymdatorn, med de tester som finns. Ta först en kopia av hela `%APPDATA%\Lopanalys` | Alla idrottare och tester finns kvar och öppnas. Filmerna heter `session-<nummer>-…mp4`, nyckelbilderna syns, `lopanalys.sqlite.v3.bak` finns, och inget efternamn står i något filnamn | |
| Filmen tas bort | Sätt *Film från testerna* på *Ta bort direkt efter analysen* och kör ett löptekniktest med GoPron | Filmen och `.mp4.json` är borta ur `recordings` när analysen är klar, nyckelbilderna och fynden finns kvar | |
| Utan samtycke till film | Kör ett löptekniktest med en idrottare utan rutan *Film under testet* | Programmet säger att inget spelas in, livebilden visas, och `recordings` får ingen ny fil | |
| Utdraget | *Exportera allt* för en idrottare med film | Zip-filen öppnas i Windows, `sammanstallning.html` går att läsa i webbläsaren och filmen spelas | |
| Testet sparas hela tiden | Starta ett test med GoPron, gå till *Inställningar* mitt i och efter testet, stäng sedan programmet i Aktivitetshanteraren mitt i ett annat test | Testet finns under *Historik*; vid nästa start frågar programmet *Ett test avbröts …*, och stegen, laktatvärdena och pulsen fram till ungefär 10 s före är kvar | |
| Fortsätta ett avbrutet test | Döda programmet mitt i en laktattrappa, starta igen, *Fortsätt från steg N*, gå igenom guiden | Bandet startar på nästa steg, testet blir klart i samma test med alla laktatvärden | |
| Maxpuls med löpteknik | Välj *Maxpuls, fartrappa*, sätt *Steglängd* till 60 s och kör testtypen med GoPron till *Utmattad* | Med lutningstrappan eller 30 s steg är *Nästa* grå med beskedet om 60 s. Filmen analyseras bara till och med det sista steget under 90 % av toppulsen; rapporten har maxpulsen och löptekniken | |
| Puls- och fartzoner | Kör *Puls- och fartzoner, motionär* ensam på bandet med pulsband, utan att röra datorn, och stoppa bandet på panelen när du inte orkar mer | Bandet byter fart för varje steg och går direkt över i trappan. När bandet stoppas avslutas testet av sig självt, återhämtningen mäts och bandet startar inte igen. Pulsen per steg stämmer med klockan, sambandet är *Bra* eller *Godtagbart*, och zonernas farter känns rimliga mot egna pass | |
| Bandet rapporterar farten | Samma test: jämför farten i resultatets steg med bandets display | Farten som bandet rapporterar ligger inom 0,3 km/h från den inställda. Annars hittar analysen inte stegen när testet har pausats | |
| Radera idrottare | Radera en idrottare med inspelningar och en sparad rapport | Idrottaren, testerna, filmerna med `.mp4.json`, bildmappen och rapporten är borta, och programmet listar det som det inte når. Windows låser öppna filer: prova också med rapporten öppen i en PDF-läsare (den ska då stå i listan) | |
| Garmin-värdena | Efter en laktattrappa: fyll i rutan *Fyll i klockan* i Garmin Connect (pulszoner baserade på % LTHR) | Fälten i Garmin Connect finns och tar emot värdena (maxpuls, LTHR, vilopuls, fem zoner i slag/min, tröskeltempo); klockan visar samma zoner som rapporten | |
| Uppskattat VO2max | Jämför värdet efter ett maxtest med klockans VO2max eller ett labbtest | Värdet ligger inom det angivna intervallet, eller *Kan inte uppskattas ur det här testet* med skälet; rapporten visar klockans värde bredvid | 2026-11-14: lutningstrappan gav ett värde långt över klockans. Rättat: inget värde utan maxkriterierna, inte ur ett sista steg över 5 % eller under 2 min |
| Trappan förbi bandets max | Fyll i 18 km/h och 12 % under *Inställningar*, kör lutningstrappan förbi steg 12 | Steg N har N % lutning; från steg 13 ökar farten 0,5 km/h per steg och TV:n säger det; loggen har `stage N: … km/h, … %` för varje steg | 2026-11-14: löparen nådde 12 % och belastningen slutade öka |
| Slutspurten av sig själv | Kör fartrappan med rutan *Slutspurt* ensam på bandet | Spurten startar utan knapptryck när pulsen planat ut eller efter 12 min, och bandet går sedan ner till gångfart. Raden under *Slutspurt* på operatörssidan följer pulsens ökning och tiden kvar till 12 min medan trappan går, säger sedan när och varför spurten startade, och syns inte på TV:n | |
| Maxpulsen sänks inte | Ett maxtest där toppen är lägre än idrottarens sparade maxpuls | Frågan *Behåll …?* kommer en gång, *Ja* behåller; zonerna säger vilken maxpuls de bygger på | 2026-11-14: en lägre topp ersatte inte den sparade maxpulsen, men det syntes inte vad zonerna byggde på |

## Rapporten och utskriften

| Del | Prov | Godkänt när | Läge |
|---|---|---|---|
| Utskrift | Skriv ut den samlade rapporten på RICOH SP C262SFNw i färg | Pappret ser ut som PDF:en: inget beskuret, bilderna skarpa, sidnumren rätt | Delvis 2026-11-13: en testrapport i färg på A4 såg bra ut, efter att skrivarens förval i Windows ställts på färg. Den samlade rapporten med bilder är inte utskriven |
| Dialogen | Skriv ut utan förvald skrivare | Windows utskriftsdialog öppnas; Avbryt skriver inget | |
| PDF på Windows | Spara laktat-, maxpuls- och löpteknikrapporten som PDF | Samma sidbrytningar som i utvecklingsmiljön, typsnittet Source Sans, svenska tecken rätt | |
| Riktiga bilder | Löpteknikrapport från en riktig GoPro-film | Stillbilderna skarpa i utskrift, ringarna syns, bildtexterna stämmer | |
| Träningsschemat | Sidan *Träningsschema* på gymdatorn: fyll i, förhandsvisa, spara som PDF och skriv ut | Förstasidan ryms på en sida, varje vecka har ett eget blad utan avhuggna rader, zonfärgerna syns | |
| Kalenderfilen | Spara *Kalenderfil* från sidan *Träningsschema* och öppna den i en iPhone och en Android-telefon | Passen hamnar på rätt dag och klockslag, med å, ä och ö rätt i beskrivningen. En ny import dubblerar inget | |
| Träningsschemat med flera idrotter | Sidan *Träningsschema* på gymdatorn: kryssa i cykel och SkiErg, välj roller och andelar och två styrkepass, begränsa en dag till cykel, förhandsvisa, spara, öppna igen och spara som PDF | Raden *Idrotter* och knappen per dag ryms i formuläret, allt läses tillbaka, förstasidan med idrottstabellen och watten under *Dina zoner* ryms på en sida och idrottsfärgerna syns i utskriften | |
| Schemat med e-post | *Skicka till idrottaren* på sidan *Träningsschema* till en egen inkorg, och öppna bilagorna i en iPhone och en Android-telefon | Mejlet kommer fram (inte i skräpposten) med PDF:en och kalenderfilen. Kalenderfilen går att öppna ur mejlet med anvisningen i texten | |
| Om testerna | Sidan *Testerna*: skriv ut *Om testerna* och spara som PDF, för *Löpning*, *Cykel* och *SkiErg* | Varje sport får plats på en A4, texten går att läsa på papper | |

## FIT-filen och mejlet

I testsviten läses FIT-filen tillbaka med kontrollsummorna, och mejlet går till en låtsad SMTP-server på samma dator. Inget riktigt mejl är skickat, och ingen tjänst har tagit emot filen.

| Del | Prov | Godkänt när | Läge |
|---|---|---|---|
| Lösenordet i Windows | *Inställningar → E-post* på gymdatorn: skriv applösenordet, lämna fältet, stäng programmet och starta det igen | Fältet visar *Sparat. Skriv ett nytt för att byta*, och lösenordet står inte i klartext i `lopanalys.sqlite` (sök efter det i filen). Windows dataskydd (DPAPI) är inte provat alls: det går bara på Windows | |
| Provmejl | Ett eget Gmail-konto för verksamheten med tvåstegsverifiering och applösenord. Förvalen `smtp.gmail.com`, 587, STARTTLS. *Skicka ett provmejl* | Mejlet kommer fram till kontot, avsändarnamnet stämmer, och raden under knappen säger att det är skickat. Fel lösenord ger beskedet om applösenord || Godkänt 2026-11-15 på gymdatorn (`dev-eba6e23`): loggen säger *test mail sent*, och mejlet fanns i Tröskels Gmail. Det är förvirrande: Gmail lägger mejl till sig själv bara under Skickat, och det är lätt att först leta i sin egen inkorg. Förslag till nästa bygge: provmejlet går till en adress som coachen anger |
| Mejl till idrottaren | Lägg din egen adress på en testperson med rutan *Kontakt om uppföljning*, kör ett test och tryck på *Skicka till idrottaren* | Rutan visar adressen och bilagorna, mejlet kommer fram med PDF:en och FIT-filen, och testet visar *Skickat till idrottaren …*. Utan rutan är knappen grå med skälet bredvid | |
| Loggen | Läs `logs\lopanalys.log` efter proven ovan | Varken adressen eller lösenordet står där, bara testets nummer | |
| Samlad rapport med bilder | *Historik → Samlad rapport → Skicka till idrottaren* med ett löpteknikstest med nyckelbilder | Mejlet går iväg (bilagorna under 20 MB) och PDF:en ser ut som den sparade | |
| FIT-filen i Strava | *Spara FIT-fil* efter en laktattrappa på bandet, ladda upp på strava.com (*Ladda upp aktivitet → Fil*) | Passet blir en löpning på löpband med rätt datum och längd, pulsen och kadensen (steg/min, inte halva) syns, och sträckan stämmer med bandet | |
| FIT-filen i Garmin Connect | *Importera data* i Garmin Connect på webben med samma fil | Filen tas emot, varven är ett per steg med vilovarv emellan, och pulskurvan stämmer med resultatet. Garmin Connect kan vara kinkigare än Strava med filer från andra tillverkare: filen har tillverkaren *development* och inget `device_info` | |
| FIT-filen för cykel och SkiErg | *Spara FIT-fil* efter en laktattrappa på cykeln och en på SkiErgen, ladda upp båda på strava.com och i Garmin Connect | Cykelfilen blir ett cykelpass inomhus med effekt, kadens och puls. SkiErg-filen tas emot. Vilken aktivitetstyp tjänsterna ger *träningsmaskin, skidåkning inomhus* är inte känt, och ska skrivas här | |
| Exe-filen | Windows-bygget och egenkontrollen | Egenkontrollen listar `lopanalys.share.fit`, `lopanalys.share.mail`, `lopanalys.share.secret`, `smtplib` och `ssl` som ok, och provmejlet går från den installerade exe-filen (certifikaten läses ur Windows) | |

## Programmet

| Del | Prov | Godkänt när | Läge |
|---|---|---|---|
| TV-vyn | Livevyn på TV:n som andra skärm; F11 och Esc | Helskärm på TV:n, F11 växlar, Esc lämnar, siffrorna går att läsa på 3 m | |
| Guiden | Hela guiden med GoPro, FTMS-band och pulsband, för *Kombinerat* och för *Maxpuls* | *Kombinerat*: alla sex stegen går igenom, ramkontrollen blir grön när löparen står rätt. *Maxpuls*: fem steg, ingen fråga om kamera och ingen ram i *Kontroll* | |
| Bluetooth | Sökning och anslutning | Fönstret går att använda under sökningen, fel visas på svenska | |
| Ljudet | Ljudsignal före stegbyte | Tre korta pip och ett långt hörs vid varje stegbyte | |
| Första starten | Start utan modell | Nedladdningsfönstret visar förloppet och livevyn startar efteråt | |
| Skalning | 125 % och 150 % på den bärbara skärmen | Ingen text klipps och ikonerna är skarpa | |
| Datamappen | *Flytta mapp* till en annan disk, säkerhetskopiera och exportera | Programmet stängs, den nya mappen används vid nästa start med alla filmer och bilder, den gamla mappen är tömd, och CSV:n öppnas i Excel | |
| Låset | Ange PIN-kod, stäng och starta programmet, vänta ut *Lås efter vila*, och starta med `--reset-pin` | Programmet öppnas låst, låser sig efter vilan men inte under ett test, och `--reset-pin` tar bort koden | |
| Kundläget | Starta ett test med TV:n inkopplad | *Idrottare*, *Nytt test* och *Historik* är dolda under testet, och TV:n visar bara förnamnet | |
| Exe-filen med cryptography | Windows-bygget och egenkontrollen | Bygget går igenom, egenkontrollen listar `cryptography.hazmat.primitives.ciphers.aead` som ok, och *Säkerhetskopiera* fungerar i den installerade exe-filen | |
| Videoanalysen | Analys efter ett löptekniktest | Analysen går i bakgrunden, fynden och nyckelbilderna visas och sparas | |
| Engelska | Starta med `--lang en` och gå igenom alla sidor | Ingen svensk text kvar, inget klipps | |

## Appen i telefonen

Appen för idrottaren ([Appen i App Store och Google Play](app/butikerna/butikerna.md)) är provad med Vitest och byggd i GitHub Actions, men har inte körts i en riktig telefon eller surfplatta.

| Del | Prov | Godkänt när | Läge |
|---|---|---|---|
| Webbappen på hemskärmen | Öppna `generaltgul.github.io/troskel/app/` i Safari på en iPhone och i Chrome på en Android-telefon, lägg den på hemskärmen och öppna en länk från Tröskel | Schemat syns, appen startar från hemskärmen utan adressfält och fungerar i flygplansläge | |
| APK:n på Android | Installera APK:n från körningen *App* i GitHub Actions | Appen startar med ikonen och startbilden, och länken, filen och QR-koden ger schemat | |
| QR-koden | *Läs en QR-kod* i APK:n och på iPhone, med koden från Tröskel | Appen frågar om kameran första gången, läser koden och sparar schemat; nekad kamera ger ett begripligt besked | |
| Kalenderfilen | *Lägg passen i kalendern* i APK:n och på iPhone | Telefonens kalender öppnar filen och passen hamnar på rätt dagar och tider | |
| Mina tider | Ändra en dags minuter, ta bort en dag och lägg in ett datum som inte går under *Mina tider*, i APK:n och på iPhone, med ett riktigt schema från Tröskel | Appen säger att schemat är omräknat från i morgon, passen från i morgon följer de nya tiderna, dagens och gårdagens pass är orörda. Motorn är provad mot Python med Vitest (`app/test/engine.test.ts`), inte i en telefon | |
| Lagringen | Installera APK:n och appen från TestFlight, läs in ett schema, ändra språket och spara *Mina tider*. Stäng appen helt och öppna den igen; installera sedan en ny version över den gamla. Välj till sist *Ta bort all data på enheten* och starta om appen | Schemat, tiderna och språket finns kvar efter omstarten och efter uppdateringen, och är borta efter borttagningen. Preferences, flytten från `localStorage` och borttagningen är provade med Vitest (`app/test/storage.test.ts`), inte i en telefon | |
| TestFlight | Bygget till TestFlight med App Store Connect-nyckeln | Bygget kommer fram i App Store Connect och går att installera med TestFlight | |
| Play internt test | Första AAB:n för hand i Play Console, sedan *Ladda upp* i arbetsflödet | Utkastet finns på spåret för internt test | |
