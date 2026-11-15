# Bikefit och stakteknik: mått, riktvärden och vad kameran klarar

*Underlag för fler sporter, skrivet 2026-11-14. Cykelns och SkiErgens tester och enheter är byggda (se [Nuläget](ombyggnad.md#nuläget)), och bikefit och stakteknik är egna testtyper i guiden. [Nuläget i koden](#nuläget-i-koden) säger vad som finns och var koden avviker från förslagen. Sidan säger vad som ska mätas ur video för bikefit (cykel på trainer) och stakteknik (Concept2 SkiErg), vilka riktvärden som finns, hur ett pedalvarv och ett drag hittas, och vad MediaPipe klarar. Syskonsidor: [Ombyggnad](ombyggnad.md) (analysprofilen per sport, steg 4 och 5) och [Enheter](enheter.md) (trainer och PM5).*

**Så här ska uppgifterna läsas:**
- *(läsaren)* = bekräftat i en sökning 2026-11-14. Läsaren läser sökträffar, inte alltid artikeln i fulltext.
- *ej verifierat mot källa* = skrivet ur minnet. Kontrolleras mot originalet innan värdet blir en gräns i koden.
- Alla gränser är **riktvärden för en observation i 2D-video, inte en diagnos**, på samma sätt som i `pose/findings.py`.

<!-- toc -->
## Innehåll

- [Sammanfattning](#sammanfattning)
- [Nuläget i koden](#nuläget-i-koden)
- [1. Bikefit](#1-bikefit)
  - [Pedalvarvet (motsvarar steghändelserna)](#pedalvarvet-motsvarar-steghändelserna)
  - [Mått, riktintervall och råd](#mått-riktintervall-och-råd)
  - [Landsväg, tempo och MTB](#landsväg-tempo-och-mtb)
- [2. Stakteknik på SkiErg](#2-stakteknik-på-skierg)
  - [Dragets faser](#dragets-faser)
  - [Draget ur handledens bana](#draget-ur-handledens-bana)
  - [Mått och riktvärden](#mått-och-riktvärden)
  - [SkiErg mot snö](#skierg-mot-snö)
- [3. Vad MediaPipe klarar och inte](#3-vad-mediapipe-klarar-och-inte)
  - [Kamerauppställning](#kamerauppställning)
- [4. Förslag på mätvärdestabeller](#4-förslag-på-mätvärdestabeller)
  - [Cykel (landsväg)](#cykel-landsväg)
  - [SkiErg](#skierg)
- [5. Nyckelbilder för rapporten](#5-nyckelbilder-för-rapporten)
  - [Cykel](#cykel)
  - [SkiErg](#skierg-1)
- [6. Besluten och frågorna som är kvar](#6-besluten-och-frågorna-som-är-kvar)
- [Källor](#källor)
<!-- /toc -->

## Sammanfattning

- **Bikefit har ett enda mått med god forskningsgrund: knävinkeln i nedersta läget.** 25–35° knäflexion mätt statiskt motsvarar ungefär 33–43° mätt i video under tramp *(läsaren)*. Allt annat (höft, bål, axel, armbåge, knä över pedalaxel) är branschpraxis utan vedertagna optimala värden, och ska visas som mätvärden med försiktiga intervall, inte som fel.
- **Stakteknikens forskning gäller snö och rullskidor, inte SkiErg, och läsaren hittade inga gradtal.** Holmberg m.fl. 2005 ger riktningen (snabba åkare böjer mer i armbåge och höft och bygger kraft snabbare), Concept2 ger de vanliga felen. Gränserna i grader är erfarenhetsvärden. Staktekniken får ändå återkoppling redan vid första testet, som löpningen: det som avviker markeras i bilderna med vad man ska tänka på, och från andra testet jämförs varje värde också med idrottarens förra test.
- **MediaPipe räcker för höft och knä (fel 3–9°), inte för fotleden** (15–22° i cykling med ett liknande verktyg) *(läsaren)*. Fotledens mått visas utan bedömning. Ytterlägen underskattas, och det är just ytterlägen bikefit bygger på: en kontroll mot goniometer behövs i den egna uppställningen.
- **Cykelhändelsen är enkel i båda sporterna:** pedalvarvet ur fotledens cirkel (vevvinkel kring cirkelns mitt), draget ur handledens höjd (vändpunkterna). Trainerns kadens och PM5:ns dragtid ger facit, som Garmin gör för löpningen.
- **Kameran avgör mer än modellen:** vinkelrätt mot rörelseplanet, linsen i höfthöjd, 3–4 m bort, linjär lins, minst 60 bilder/s. En sidovy visar bara den närmaste sidan, så sidoskillnad kräver en film per sida.

## Nuläget i koden

Byggt 2026-11-14 i `pose/bike/` och `pose/ski/`, med resultatflik och rapport. Hur det hänger ihop står i [Arkitektur](../ARKITEKTUR.md#bikefit-och-stakteknik), hur det används i handboken ([Bikefit](../handbok/bikefit/bikefit.md), [Stakteknik](../handbok/stakteknik/stakteknik.md)). Allt är provat mot syntetiska cyklister och stakare, inget mot en riktig person: se [Prov](../PROV.md#bikefit-och-stakteknik).

| Del | I koden | Skillnad mot förslagen nedan |
|---|---|---|
| Pedalvarvet | Cirkel till fotledens bana, vevvinkel, varv från ÖD till ÖD. Framifrån ur knäts höjd | Klockan 3 tas där pedalen (trampdynan) är längst fram, eftersom fotledens cirkel är vriden några grader mot veven |
| Draget | Handledens höjd, start och slut där farten nedåt går mot noll | Farten följs till 10 och 30 % av toppfarten och linjen dras ut till noll. Bara en tröskel kortade varje drag med en tiondel |
| Knäflexion nederst | 33–43°, rött under 25° och över 50°, forskningsbaserat, mätfel ±5° | Som förslaget |
| Knäflexion överst | Högst 115°, erfarenhetsvärde | 115 i stället för 112: den övre änden av *cirka 110–115* |
| Höft, bål, axel, armbåge | Intervallen i tabellen, erfarenhetsvärden | Blir aldrig röda, bara *Håll koll*. De röda gränserna i avsnitt 4 används inte |
| Fotleden | Omfånget visas utan bedömning | Som förslaget |
| Knä över pedalaxel | Visas utan bedömning | Förslaget hade ett intervall. Mätfelet ±2 cm är lika stort som intervallet, så värdet bedöms inte |
| Höftens höjdrörelse | Högst 2 cm, erfarenhetsvärde, mätfel ±1,5 cm | Räknas ur en sinusvåg passad till varvet, så att brus inte räknas som rörelse |
| Knäts spår framifrån | Sidorörelse per varv, sämsta sidan, och om knät går inåt i nedtrampet eller utåt överst | `knee_valgus_deg` och bäckenets gung är inte byggda. Bakifrån väntar |
| Sidoskillnad | Inte byggd | Kräver en film per sida |
| Cykeltyp | Bara landsväg | Tempo och MTB väntar |
| Förslag på cykeln | Sadelhöjd i mm ur knävinkeln och benets längd, klossar ur knäts spår, styrstam ur armbåge, axel och höft | Skalan tas ur kroppslängden. Vevarmens längd används inte |
| Stakteknikens mått | Alla i tabellen i avsnitt 4, plus dragtid, återgång, minsta armbågsvinkel och taktens variation | Alla intervall märks *erfarenhetsvärde*. Händerna förbi låren mäts inte |
| Nyckelbilder | Cykel: nedersta läget, översta läget, klockan 3, ställningen, pedalvarvet, knäts spår. SkiErg: start, mitt i draget, slutläge, återgång, dragets faser | `rock` (bäckenet) finns inte |
| Manuell checklista | Samtal, rörlighet, fötter, ben, klossar, cykelns mått före och efter, åtgärder | Ny: fylls i på resultatfliken och skrivs ut |
| Jämförelse | Varje värde mot förra testet, *Oförändrat* inom mätfelet | Gäller båda sporterna |
| Livebrickor på TV:n | TV:n visar bilden, skelettet, watt, puls och kadens. Inga mätvärden live | Vinklarna live är inte byggda: värdena räknas ur filmen efter testet |
| Testtyp i *Nytt test* | *Bikefit* under cykel och *Stakteknik* under SkiErg: uppvärmning utan film, sedan ett filmat steg i jämn arbetseffekt, och analysen efter testet | Ny: protokollet, se [Arkitektur](../ARKITEKTUR.md#bikefit-och-stakteknik) |

## 1. Bikefit

### Pedalvarvet (motsvarar steghändelserna)

Från sidan rör sig den närmaste fotleden i en nästan cirkelrund bana kring vevlagret. Förslag på `detect_events` för `Sport.BIKE`:

1. Passa en cirkel (eller ta medianen av banans x och y) till fotledens bana över klippet. Mitten är ungefär vevlagret. Trampar cyklisten med rörlig fotled blir banan en ellips, och det räcker ändå.
2. **Vevvinkeln** är riktningen från mitten till fotleden: 0° = överst (**ÖD**, övre dödläget, klockan 12), 90° = framåt (klockan 3, tecknet ur `facing`), 180° = nederst (**ND**, klockan 6). Vinkeln vecklas ut över tiden, och ett varv går från ÖD till nästa ÖD.
3. Kadensen är varv per minut ur den utvecklade vinkeln. Den jämförs med trainerns kadens som facit.
4. Måtten tas i lägena: knäflexion som **minsta värdet i varvet** (ligger nära ND) och **största värdet** (nära ÖD), knä över pedalaxel vid 90°, bål, axel och armbåge som medel över varvet.

Det enkla alternativet (ND = fotledens lägsta punkt, ÖD = högsta) fungerar också, men ger sämre tid i lägena eftersom banan är flack där. Framifrån finns ingen cirkel: där hittas varvet ur knäts höjd (högst = ÖD för det benet).

Bildfrekvens: vid 90 varv/min tar ett varv 667 ms. Med 30 bilder/s är det 18° vevvinkel mellan bilderna, med 60 är det 9° och med 120 är det 4,5°. **60 bilder/s är lägsta nivån, 120 är att föredra.**

### Mått, riktintervall och råd

Knäflexion = 180° − vinkeln höft–knä–fotled (samma definition som löpningens `knee_flex_*`). Bålvinkel = linjen höft–axel mot horisontalen. Höftvinkel = vinkeln axel–höft–knä. Axelvinkel = vinkeln höft–axel–armbåge. Armbågsflexion = 180° − vinkeln axel–armbåge–handled.

| Mått | Riktintervall (landsväg) | Källa | Avvikelse → råd |
|---|---|---|---|
| Knäflexion i nedersta läget | Statiskt 25–35° (Holmes m.fl. 1994). **I video under tramp 33–43°** *(läsaren)*; Ferrer-Roca m.fl. 2012 använde 30–40° som dynamiskt intervall, och 26 % av cyklisterna låg utanför trots sadelhöjd efter innerbenslängd *(läsaren, i andra hand)*. Uppmätt hos 19 vältränade cyklister: 36 ± 7° (Holliday & Swart 2021) *(läsaren)* | Swart & Holliday 2019; Bini & Hume 2016: skillnaden statiskt mot dynamiskt i klockan 6 är knä 8 ± 2°, höft 5 ± 1°, fotled 9 ± 2° *(läsaren)* | Över intervallet (för böjt): **sadeln upp**. Under (för rakt), eller höften gungar och tån pekar nedåt: **sadeln ner**. Ändra 3–5 mm i taget och mät om. Geometriskt ger 1 cm sadelhöjd cirka 3–4° i knät, mindre i praktiken eftersom fotleden kompenserar (*ej verifierat mot källa*) |
| Knäflexion i översta läget | Högst cirka 110–115° (*ej verifierat mot källa*; läsaren hittade inget värde) | Branschpraxis | Över: knät trycks ihop överst. **Sadeln upp** om nedersta läget tillåter, annars **kortare vevarmar** eller sadeln bakåt |
| Höftvinkel i översta läget (axel–höft–knä, minsta värdet) | Minst cirka 45° på landsväg (*ej verifierat mot källa*). Äldre rekommendation 55–65° med annan definition *(läsaren)*. Uppmätt medel 77 ± 5° (Holliday & Swart 2021, definitionen inte kontrollerad) *(läsaren)* | Optimala värden saknas (Swart & Holliday 2019) *(läsaren)* | Under (höften stängd, knät slår mot bålen): **styret upp eller närmare**, sadeln framåt, kortare vevarmar |
| Bålvinkel mot horisontalen | Motion 50–60° (en källa, erfarenhetsbaserad) *(läsaren)*. Landsväg, händer på bromsgreppen 40–50° (*ej verifierat mot källa*) | Erfarenhetsvärden | Lägre än cyklisten orkar hålla (ryggen rundas, nacken spänns): **styret upp, kortare styrstam**. Mer upprätt än önskat: styret ner eller längre styrstam, i små steg |
| Axelvinkel (höft–axel–armbåge) | Cirka 80–95° (*ej verifierat mot källa*). Uppmätt 112 ± 7° med Holliday & Swarts definition *(läsaren)* | Optimala värden saknas *(läsaren)* | Över (översträckt, tyngd på händerna): **kortare styrstam**. Under (ihopkrupen): längre styrstam |
| Armbågsflexion | 10–25°: lätt böjd, aldrig låst. Uppmätt 19 ± 8° *(läsaren)* | Holliday & Swart 2021 | Raka, låsta armar: styret för långt bort eller för lågt → **kortare styrstam eller styret upp** |
| Fotledens rörelse (vinkeln knä–fotled–tå, omfång över varvet) | Omfång cirka 15–25°. Ingen bedömning i programmet | *Ej verifierat mot källa*; läsaren hittade inget | Stort omfång med tån nedåt i nedersta läget tyder på **för hög sadel**. Hälen sjunker djupt: för låg sadel |
| Knä över pedalaxel (klockan 3) | Knäts framkant 0–2 cm bakom pedalaxeln (*ej verifierat mot källa*). Tumregel utan forskningsstöd; läsaren hittade ingen tolerans | Branschpraxis | Knät långt framför: **sadeln bakåt**. Långt bakom: sadeln framåt. Sadelhöjden mäts om efteråt, eftersom den ändras med sadelns läge |
| Knäts spår framifrån | Knät går rakt över foten. Sidorörelse högst cirka 3 cm per varv (*ej verifierat mot källa*) | Branschpraxis | Knät faller inåt i nedtrampet: kontrollera klossarnas vinkel, pedalavstånd och inlägg. Knät går utåt överst: ofta **för låg sadel** eller stängd höft |
| Höftgung | Från sidan: höften rör sig högst cirka 2 cm i höjd per varv. Fram- eller bakifrån: bäckenet lutar högst cirka 5° (båda *ej verifierat mot källa*) | Branschpraxis | Gung är det tydligaste tecknet på **för hög sadel**. Ensidigt gung: benlängdsskillnad eller sned sadel |
| Sidoskillnad | Knäflexion i nedersta läget skiljer högst 5° mellan sidorna (*ej verifierat mot källa*) | Branschpraxis | Kräver en film per sida. Skillnaden ligger nära mätfelet (avsnitt 3), så bara stora skillnader rapporteras |

**Det läsaren inte hittade i två sökningar:** knäflexion i övre dödläget, bålvinkel uppdelad på landsväg, tempo och MTB, Retüls referensvärden, fotledens vinkel och omfång, tolerans för knä över pedalaxel, axel- och armbågsvinkel som mål och knäts sidorörelse i mm. Raderna ovan för dessa är erfarenhetsvärden. De går bara att belägga genom att läsa originalen (Holmes 1994, Silberman 2005, Retüls handledning) sida för sida.

### Landsväg, tempo och MTB

Benens mått (knäflexion i nedersta läget) är i stort sett desamma i alla tre, eftersom de följer benet och sadelhöjden. Skillnaden ligger i överkroppen. Alla värden i tabellen är *ej verifierade mot källa*.

| | Landsväg | Tempo och triathlon | MTB |
|---|---|---|---|
| Bålvinkel mot horisontalen | 40–50° (motion 50–60°) | 15–30° | 45–60° |
| Höftvinkel i översta läget | Minst cirka 45° | Stängs av den låga bålen. Sadeln flyttas framåt för att öppna den | Öppen, sällan ett problem |
| Armar | Armbågsflexion 10–25° | Underarmen i armstöd, armbågen cirka 90–110°, överarmen nära lodrät | Mer böjda armbågar, bredare styre |
| Knä över pedalaxel | 0–2 cm bakom | Gäller inte: knät ligger avsiktligt framför | Som landsväg eller något bakom |

Förslag: profilen får ett val *cykeltyp* (landsväg, tempo, MTB) som väljer intervallen, på samma sätt som löpningens `reference_ranges` tar fart och längd.

## 2. Stakteknik på SkiErg

### Dragets faser

| Fas | Vad som händer | Kännetecken i bild |
|---|---|---|
| 1. Start (fattning) | Händerna högst, kroppen sträckt, vikten på framfoten, ofta uppe på tå | Handleden i sin högsta punkt, strax över ögonhöjd |
| 2. Drag | Bålen fälls först ("magen startar draget"), sedan följer armarna med låst armbågsvinkel, sist sträcks armbågen | Handleden rör sig nedåt, höften böjs |
| 3. Slutläge | Händerna vid låren (inte bakom), bålen framåtfälld med neutral rygg, knäna lätt böjda | Handleden i sin lägsta punkt |
| 4. Återgång | Höft och knä sträcks, armarna förs upp och fram, kroppen reser sig mot tå | Handleden rör sig uppåt |

### Draget ur handledens bana

Förslag på `detect_events` för `Sport.SKIERG`, med kameran från sidan:

1. Ta den närmaste handledens höjd över tiden, lågpassfiltrerad som i `filters.py`. Kurvan svänger en gång per drag.
2. **Start** = lokal högsta punkt, **slutläge** = lokal lägsta punkt. Kräv ett minsta avstånd mellan topp och botten (till exempel 15 % av kroppslängden i bild) och minst 0,6 s mellan två starter, så att små ryck inte räknas.
3. Tiden förfinas mellan bilderna med samma metod som `gait._extrapolate`: draget börjar när handleden passerar en tröskel för nedåtfart, inte i själva toppen, där kurvan är flack.
4. **Dragtid** = start till slutläge, **återgång** = slutläge till nästa start, **takt** = 60 / (dragtid + återgång).
5. PM5 skickar dragtid och återgångstid per drag (0x0035, se [Enheter](enheter.md)) och är facit för tiderna. Vinklarna tas i start, i slutläget och som ytterlägen under draget.

### Mått och riktvärden

Bålens fällning = linjen höft–axel mot lodlinjen (löpningens `trunk_lean_deg`). Höftflexion = 180° − vinkeln axel–höft–knä. Knäflexion som i löpningen. Armbågsvinkel = vinkeln axel–armbåge–handled.

| Mått | Riktvärde | Källa | Vanligt fel och råd |
|---|---|---|---|
| Handens höjd i start | Händerna strax över ögonhöjd *(läsaren)* | Concept2, *5 Common Errors on the SkiErg* | **För hög räckvidd** är ett av Concept2:s fem vanliga fel *(läsaren)*. För låg start (under hakan) ger kort drag (*egen bedömning*) |
| Armbågsvinkel i dragets början och minsta värdet under draget | Cirka 80–110° i starten, böjd och fast (*gradtalen ej verifierade mot källa*) | Holmberg m.fl. 2005 (rullskidor på band, 11 elitåkare, 85 % av maxfart): de snabbaste hade **mindre minsta armbågsvinkel** och högre vinkelhastighet i armbågens flexion *(läsaren)* | Raka armar i starten: lång hävarm, axlarna tar lasten och tröttnar fort, bålen kommer inte med → "böj och lås armbågen, häng i handtagen" |
| Bålens fällning i slutläget | Cirka 40–60° från lodlinjen (*ej verifierat mot källa*) | Erfarenhetsvärde. Concept2 beskriver fällningen som en båge med neutral rygg *(läsaren)* | För liten: bara armarna drar. För stor, eller **baken skjuts ut** (Concept2:s fel) *(läsaren)*: draget blir ett marklyft, lång återgång och belastad ländrygg |
| Höftflexion i slutläget | Cirka 50–80° (*ej verifierat mot källa*) | Holmberg m.fl. 2005: de snabbaste hade mindre minsta höftvinkel (mer böjd höft) och snabbare höftflexion *(läsaren)*. Gradtalet är ett erfarenhetsvärde | Liten höftflexion med stor knäböjning: draget blir en knäböj i stället för en bålfällning. Höftfällningen är det som försvinner först vid trötthet (HYROX-guider, låg källkvalitet) *(läsaren)* |
| Knäflexion i slutläget | Cirka 20–50°. Över 60° räknas som knäböj (*ej verifierat mot källa*) | Gradtalen är erfarenhetsvärden. Concept2: höften ska fällas före knäna, höftfällning och inte knäböj *(läsaren)* | "Sitter ner": tyngdpunkten sänks utan att handtagen dras → "fäll i höften, mjuka knän" |
| Dragtid | Mäts, jämförs med PM5. På snö cirka 0,25–0,35 s i hög fart (*ej verifierat mot källa*), på SkiErg längre: cirka 0,6 s | 0,6 s ur [Enheter](enheter.md). Läsaren hittade inga tider | Kort, ryckigt drag: handtagen släpps för tidigt. **Draget för långt bakåt** (händerna förbi låren) är Concept2:s fel *(läsaren)* |
| Drag : återgång | Återgången längre än draget: cirka 1 : 2 enligt Concept2 *(läsaren)*, cirka 1 : 1,5 (0,6 s och 0,9 s) som erfarenhetsvärde | Concept2:s tekniksida; [Enheter](enheter.md). Holmberg: de snabbaste hade längre relativ återgång (konferensversionen) *(läsaren)* | Återgången kortare än draget: jäktad takt, ingen vila mellan dragen |
| Takt | Distans 35–45 drag/min, intervaller 45–60 (*ej verifierat mot källa*) | Erfarenhetsvärde | Hög takt med låg effekt per drag: korta drag |
| Tyngdpunktens höjdrörelse (höftens mitt) | Mäts, cirka 10–25 cm. Ingen bedömning i första versionen | *Ej verifierat mot källa* | Nästan ingen rörelse: kroppsvikten används inte. Mycket stor: knäböj |
| Tåhävning i start | Hälen lyfter 0–5 cm. Ingen bedömning | Danielsen m.fl. 2015 om benens bidrag i stakning, *ej verifierat mot källa* | Platt fot hela draget: kroppen faller inte in i draget |
| Ordningen bål före armar | Höftflexionen börjar före eller samtidigt med att armbågen sträcks | Holmberg m.fl. 2005: ledvinklarnas minimum (armbåge, höft, knä, fotled) inföll kring stavkraftens topp *(läsaren)*. Ordningen i tid är *ej verifierad mot källa* | Armarna sträcks först: "armdrag", liten effekt |

### SkiErg mot snö

*Ej verifierat mot källa* där inget annat står. **Läsaren hittade inga siffror för skillnaden.** Närmast är Halonen m.fl. 2015 (*Science and Skiing VI*), som jämför cykelns tider, kraft och muskelaktivering på snö och ergometer, men resultaten syntes inte i träffarna. Holmberg & Nilsson 2008 fann stark korrelation i effekt mellan ergometer och snö i ett sexminuterstest *(läsaren)*.

- På snö faller åkaren framåt över stavarna och stavarna står i marken. På SkiErg står fötterna stilla och handtagen dras nedåt mot ett svänghjul. Kroppen lutar därför mindre framåt i starten, och draget är längre i tid.
- Draget på SkiErg går rakare nedåt och ska sluta vid låren (Concept2) *(läsaren)*. På snö fortsätter händerna bakom höften. Armbågens sträckning i slutet är därför mindre på SkiErg.
- Holmbergs resultat (snabba åkare: mindre minsta vinkel och högre vinkelhastighet i armbåge och höft, högre toppkraft, kortare tid till toppkraften, relativt lång återgång) *(läsaren)* går att använda som **riktning** för råden, men inte som gränsvärden i grader för SkiErg.
- Följden för programmet: stakteknikens intervall blir breda, och rapporten lyfter i första hand **förändring mot idrottarens förra test** och ordningen i rörelsen.

## 3. Vad MediaPipe klarar och inte

| Fråga | Läget |
|---|---|
| Noggrannhet, höft och knä | MediaPipe 3–9° mot markörer (Sport Sciences for Health 2023, inte kontrollerad i detalj). OpenPose i cykling från sidan: 4–8° i höft och knä (Bini m.fl. 2023, 26 cyklister). Löpning utomhus mot manuell mätning: medelfel 2,1° *(läsaren)* |
| Noggrannhet, fotled | 15–22° i cykling med OpenPose, dålig till måttlig överensstämmelse *(läsaren)*. **Fotledens mått visas utan bedömning** |
| Ytterlägen | Största flexion och extension underskattas *(läsaren)*. Det drabbar just nedersta och översta läget. Statisk knäflexion med en kamera hade fel på 15° i en studie *(läsaren)* |
| 2D från sidan | En vinkel stämmer bara när leden rör sig i bildplanet. Knät som går utåt, eller en kamera som står snett, ger en för liten vinkel. Felet växer mot bildens kanter |
| Skymd bortre sida | Bortre ben och arm gissas. Bara den närmaste sidan mäts (som `_near_side` i `annotate.py`). Sidoskillnad kräver en film per sida |
| Cykeln skymmer | Ramen, veven och kedjekransen skymmer den bortre foten helt och ibland den närmaste hälen. Höftens landmärke är osäkert på en sittande, framåtlutad cyklist (sadeln och byxan skymmer). Händerna på styret tappas ofta |
| SkiErgen skymmer | Från sidan står stolpen framför åkaren och skymmer lite. I slutläget passerar den bortre handen bakom kroppen. Den närmaste handleden syns hela draget |
| Bildfrekvens | Läsaren hittade ingen rekommendation. Cykel: minst 60, helst 120 bilder/s (se pedalvarvet). SkiErg: 60 räcker för faserna (draget tar cirka 0,6 s), 120 för armbågsvinkeln i starten. Kort slutartid (1/500 s eller kortare) och gott ljus, annars suddas foten och handen (*ej verifierat mot källa*) |
| Skala i cm | Som löpningen: ur kroppslängden, ±5–8 % (`ScaleInfo`). En sittande cyklist ger sämre skala än en stående löpare, så cm-måtten (knä över pedalaxel, höftgung) får större osäkerhet. Ett bättre alternativ för cykeln: vevarmens längd är känd (165–175 mm, står på veven), och radien i den passade cirkeln ger då bildpunkter per centimeter direkt (*eget förslag*) |
| Pedalaxeln | Är inget landmärke. Den uppskattas ur fotens främre del (`foot`-landmärket) eller ur cirkelns mitt och vevlängden. Knä över pedalaxel får därför en osäkerhet på cirka ±2 cm (*egen uppskattning*) |

**Slutsats:** programmet ska visa osäkerheten (cirka ±5° på höft och knä), inte ge råd på skillnader under den, och **kontrolleras mot goniometer** i den egna uppställningen innan gränserna låses, så som löpningen provas mot Garmin.

### Kamerauppställning

| | Bikefit från sidan | Bikefit framifrån | SkiErg från sidan |
|---|---|---|---|
| Riktning | Vinkelrätt mot cykelns längdriktning, siktad mot sadelns framkant | I cykelns längdlinje, mitt framför framhjulet | Vinkelrätt mot dragets plan, siktad mot höften |
| Linsens höjd | Höfthöjd på cykeln, cirka 90–100 cm | Knähöjd, cirka 60–80 cm | Cirka 100–110 cm (höften i stående) |
| Avstånd | 3–4 m | 2,5–3,5 m | 3–4 m |
| Utsnitt | Hela cyklisten och båda hjulen, liggande bild | Från pedalerna till axlarna, stående bild | Från golvet till händerna i högsta läget (cirka 2,3 m) med marginal, stående bild |
| Lins | GoPro *Linear*, inte *Wide* (vidvinkeln böjer linjerna och förstör vinklarna) | Samma | Samma |
| Bildfrekvens | 120 (minst 60) | 60 | 60–120 |
| Övrigt | Cykeln i våg, trainern rakt. Åtsittande kläder. Fem minuters uppvärmning och sedan film i arbetsfart, eftersom ställningen ändras med belastningen | Bakifrån visar höftgunget bättre än framifrån, där styret och händerna skymmer höften | Samma plats för fötterna varje gång (tejp på golvet), så att tester går att jämföra |

Avstånd, höjd och bildfrekvens är *ej verifierade mot källa*. De följer samma princip som löpningens uppställning: långt avstånd och smal bildvinkel minskar perspektivfelet.

## 4. Förslag på mätvärdestabeller

I samma anda som `live_targets` och `reference_ranges` i `pose/findings.py`: en `MetricRange` per nyckel med `ok_lo`/`ok_hi` (grönt) och `fix_lo`/`fix_hi`. Intervallen nedan är förslag och ska kontrolleras mot källorna i avsnitt 1 och 2. Rader utan intervall visas som värde utan färg. Vyer: `side` = `View.SIDE_LEFT`/`SIDE_RIGHT`, `front` = `View.FRONT`, `rear` = `View.REAR`.

### Cykel (landsväg)

| Mätvärde | Nyckel | Enhet | Grönt | Rött | Vy |
|---|---|---|---|---|---|
| Kadens | `cadence_rpm` | varv/min | – (visas) | – | alla |
| Knäflexion nederst | `knee_flex_bottom_deg` | ° | 33–43 | under 25, över 50 | side |
| Knäflexion överst | `knee_flex_top_deg` | ° | högst 112 | över 120 | side |
| Höftvinkel överst | `hip_angle_top_deg` | ° | minst 45 | under 38 | side |
| Bålvinkel | `trunk_angle_deg` | ° | 40–55 (tempo 15–30, MTB 45–60) | – | side |
| Axelvinkel | `shoulder_angle_deg` | ° | 80–95 | över 105 | side |
| Armbågsflexion | `elbow_flex_deg` | ° | 10–25 | under 3 (låst) | side |
| Fotledens omfång | `ankle_range_deg` | ° | – (visas) | – | side |
| Knä över pedalaxel | `knee_over_pedal_cm` | cm | −2–0 (minus = bakom) | över +2, under −4 | side |
| Höftens höjdrörelse | `hip_bounce_cm` | cm | högst 2 | över 4 | side |
| Knäts sidorörelse | `knee_lateral_cm` | cm | högst 3 | över 5 | front |
| Knät inåt (som löpningens FPPA) | `knee_valgus_deg` | ° | högst 10 | över 15 | front |
| Bäckenets gung | `pelvic_rock_deg` | ° | högst 5 | över 8 | rear, front |
| Sidoskillnad knä nederst | `asymmetry["knee_flex_bottom_deg"]` | ° | högst 5 | över 8 | side, två filmer |

### SkiErg

| Mätvärde | Nyckel | Enhet | Grönt | Rött | Vy |
|---|---|---|---|---|---|
| Takt | `stroke_rate_spm` | drag/min | – (visas, följer passet) | – | side |
| Dragtid | `drive_ms` | ms | – (visas) | – | side |
| Återgång / drag | `recovery_ratio` | kvot | minst 1,2 | under 0,9 | side |
| Handens höjd i start | `hand_height_start_cm` | cm över näsan (`nose`) | 0–20 | under −15, över 35 | side |
| Armbågsvinkel i start | `elbow_angle_start_deg` | ° | 80–110 | över 140 (raka armar) | side |
| Bålens fällning i slutläget | `trunk_lean_end_deg` | ° | 40–60 | under 25, över 80 | side |
| Höftflexion i slutläget | `hip_flex_end_deg` | ° | 50–80 | under 30 | side |
| Knäflexion i slutläget | `knee_flex_end_deg` | ° | 20–50 | över 70 (knäböj) | side |
| Bål före armar | `trunk_lead_ms` | ms | minst 0 | under −100 (armarna först) | side |
| Tyngdpunktens höjdrörelse | `vertical_oscillation_cm` | cm | – (visas) | – | side |
| Tåhävning i start | `heel_raise_cm` | cm | – (visas) | – | side |

Nycklarna för livebrickorna (`cadence_rpm`, `stroke_rate_spm`) följer `BikeState` och `SkiErgState` i [Ombyggnad](ombyggnad.md). Livevyn bör visa högst tre teknikbrickor per sport: cykel knäflexion nederst, bålvinkel och höftens höjdrörelse; SkiErg bålens fällning, armbågsvinkel i start och återgång/drag.

## 5. Nyckelbilder för rapporten

Samma form som löpningens `KeyFrameSpec` (`kind`, `side`, rubrik, bildtext) och samma ritverktyg (`Canvas`: vinkelbåge, ring, streckad lodlinje).

### Cykel

| `kind` | Ögonblick | Det som ritas |
|---|---|---|
| `bottom` | Nedersta läget (minsta knäflexion i varvet) | Knävinkelns båge med värde och färg, fotledens vinkel i grått, ring på knät vid fynd |
| `top` | Översta läget (största knäflexion) | Knävinkeln och höftvinkeln (axel–höft–knä) |
| `forward` | Klockan 3 | Streckad lodlinje från knät till pedalen, avståndet i cm |
| `posture` | Medelställningen (klockan 3 duger) | Bålvinkeln mot en vågrät streckad linje, axelvinkeln, armbågsflexionen |
| `sequence` | Fyra bilder: klockan 12, 3, 6 och 9 | Skelettet och fotledens bana som en tunn kurva |
| `front_track` | Framifrån, mitt i nedtrampet | Knäts spår över cirka 10 varv som en kurva per ben, lodlinje från höften till foten |
| `rock` | Bakifrån eller framifrån, största bäckenlutningen | Linjen mellan höfterna mot vågrätt, med värde |

### SkiErg

| `kind` | Ögonblick | Det som ritas |
|---|---|---|
| `start` | Handleden högst | Armbågsvinkeln, handens höjd mot ögonhöjd (streckad vågrät linje genom näsan), hälens lyft |
| `mid_drive` | Handleden i axelhöjd på väg ned | Armbågsvinkeln och bålens fällning: här syns om bålen eller armarna drar |
| `finish` | Handleden lägst | Bålens fällning mot lodlinjen, höftflexion och knäflexion |
| `recovery` | Mitt i återgången | Skelettet, höftens höjd mot startläget |
| `sequence` | De fyra ögonblicken i rad | Handledens bana som en kurva över hela draget, höftens höjd som en streckad linje |

## 6. Besluten och frågorna som är kvar

**Beslutat 2026-11-14:**

- **Bikefit gäller först landsvägscykel** på trainern, filmad **från en sida och framifrån**. Tempo, MTB och vyn bakifrån väntar.
- **På plats ställs sadelhöjden och skoklossens placering.** Längre eller kortare styrstam blir en rekommendation i rapporten, som kunden ordnar själv.
- **Staktekniken analyseras som löpningen:** återkoppling med markeringar i bilderna redan första gången, och jämförelse med förra testet när det finns. Jämförelsen med förra testet gäller också löptekniken, så att det syns om utvecklingen går åt rätt håll.

**Kvar:**

1. Kan du mäta knävinkeln med en **goniometer** (eller vinkelmätare i mobilen) vid ett prov, så att programmets vinklar får ett facit innan gränserna låses?
2. Går GoPron att ställa **3–4 m vinkelrätt från sidan** av både cykeln och SkiErgen i gymmet, med hela kroppen i bild (SkiErg: händerna i högsta läget)?

## Källor

- Holmes JC, Pruitt AL, Whalen NJ. Lower extremity overuse in bicycling. Clin Sports Med 1994;13:187–205. *Ej läst i den här vändan.*
- Swart J, Holliday W. Cycling biomechanics optimization: the (r)evolution of bicycle fitting. Curr Sports Med Rep 2019;18(12):490–496. <https://pubmed.ncbi.nlm.nih.gov/31834181/> *(läsaren)*
- Holliday W, Swart J. Anthropometrics, flexibility and training history as determinants for bicycle configuration. Sports Med Health Sci 2021. <https://journal.hep.com.cn/smhs/EN/10.1016/j.smhs.2021.02.007> *(läsaren)*
- Bini RR, Hume P. A comparison of static and dynamic measures of lower limb joint angles in cycling. 2016 *(läsaren, i andra hand)*
- Bini RR m.fl. J Sports Sci 2023: OpenPose mot markörer hos 26 cyklister *(läsaren, i andra hand)*
- MediaPipe mot markördigitalisering: Sport Sciences for Health 2023. <https://link.springer.com/content/pdf/10.1007/s11332-023-01075-7.pdf> *(läsaren, inte kontrollerad i detalj)*
- Ferrer-Roca V m.fl. J Strength Cond Res 2012;26:3025–3029 (statisk mot dynamisk sadelhöjd, 30–40° dynamiskt) *(läsaren, i andra hand; originalet inte läst)*
- Holmberg HC, Lindinger S, Stöggl T, Eitzlmair E, Müller E. Biomechanical analysis of double poling in elite cross-country skiers. Med Sci Sports Exerc 2005;37(5):807–818. <https://read.qxmd.com/read/15870635/biomechanical-analysis-of-double-poling-in-elite-cross-country-skiers> *(läsaren: sammanfattningen, inga gradtal)*
- Halonen J m.fl. Biomechanics of double poling when skiing on snow and using an ergometer. Science and Skiing VI, 2015. *(läsaren: bara syftet)*
- Concept2. 5 Common Errors on the SkiErg. <https://concept2.com/blog/5-common-errors-on-the-skierg> *(läsaren: nå för högt, dra armarna för långt bak, lämna armarna kvar bakom kroppen, skjuta ut baken, stå för långt från maskinen)*. Tekniksidan: <https://www.concept2.com.au/skierg/training/technique> *(läsaren: höftfällning, drag och återgång 1 : 2)*
- Danielsen J m.fl. 2015 om benens och tyngdpunktens bidrag i stakning. *Ej verifierat mot källa.*
