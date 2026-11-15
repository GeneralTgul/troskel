# Handbok

*För dig som kör testerna: från att programmet startar till att idrottaren har rapporten i handen. Bilderna är tagna i demoläget, med simulerat band, simulerad puls och påhittade idrottare.*

<!-- toc -->
## Innehåll

- [Så går ett test till](#så-går-ett-test-till)
- [Det som inte är provat på riktig hårdvara](#det-som-inte-är-provat-på-riktig-hårdvara)

**Undersidor:**

- [Bikefit](bikefit/bikefit.md)
- [Cykeltester](cykeltester/cykeltester.md)
- [Kom igång](kom-igang/kom-igang.md)
- [Laktattrappa](laktattrappa/laktattrappa.md)
- [Löpteknik](lopteknik/lopteknik.md)
- [Maxpulstest](maxpulstest/maxpulstest.md)
- [Nytt test](nytt-test/nytt-test.md)
- [Puls- och fartzoner](puls-och-fartzoner/puls-och-fartzoner.md)
- [Resultat](resultat/resultat.md)
- [SkiErg](skierg/skierg.md)
- [Stakteknik](stakteknik/stakteknik.md)
- [Träningsschema](traningsschema/traningsschema.md)
- [Under testet](under-testet/under-testet.md)
<!-- /toc -->

## Så går ett test till

1. **Lägg upp idrottaren** med längd och, om den är känd, maxpuls och vilopuls. Se [Kom igång](kom-igang/kom-igang.md).
2. **Gå igenom *Nytt test*:** idrottare, testtyp, protokoll, kamera, band och puls, kontroll. Se [Nytt test](nytt-test/nytt-test.md).
3. **Kör testet** från operatörssidan medan idrottaren ser livevyn på TV:n. Se [Under testet](under-testet/under-testet.md).
4. **Läs resultatet,** skriv en kommentar och skriv ut rapporten, eller mejla den med testets FIT-fil. Se [Resultat](resultat/resultat.md).
5. **Bygg ett träningsschema** på testet, om idrottaren ska ha ett. Se [Träningsschema](traningsschema/traningsschema.md).

Varje test har en egen sida med det som är särskilt för det: [Laktattrappa](laktattrappa/laktattrappa.md), [Maxpulstest](maxpulstest/maxpulstest.md), [Puls- och fartzoner](puls-och-fartzoner/puls-och-fartzoner.md) och [Löpteknik](lopteknik/lopteknik.md). [Bikefit](bikefit/bikefit.md) och [Stakteknik](stakteknik/stakteknik.md) är tekniktesterna för cykel och SkiErg.

Sidorna ovan beskriver löpning på band. Testerna i watt har egna sidor: [Cykeltester](cykeltester/cykeltester.md) på en trainer och [SkiErg](skierg/skierg.md) på Concept2 SkiErg. Maxpuls, trösklar och zoner hålls isär per sport.

## Det som inte är provat på riktig hårdvara

Programmet är provat mot simulerade enheter, och handboken beskriver det som finns i koden. Det här är ännu inte provat fullt ut med riktig utrustning, och står med läget i [PROV.md](../PROV.md):

- kameran, också GoPron över USB,
- löpbandet över Bluetooth (FTMS) och över QZ, och pulsbandet,
- cykeltrainern över Bluetooth (FTMS) och SkiErgens monitor PM5: inget av det har mött riktig hårdvara,
- ett helt test på ett riktigt band: laktattrappan, maxpulstestet, *Puls- och fartzoner* och det manuella läget,
- livevyn i helskärm på en TV som andra skärm, och ljudsignalerna,
- löptekniken med en riktig kamera och en riktig löpare på bandet,
- den samlade rapporten på papper, säkerhetskopian och värdena i Garmin Connect,
- mejlet till idrottaren från ett riktigt konto, och FIT-filen i Strava och Garmin Connect.
- träningsschemat på papper och kalenderfilen i en telefon.

På sidorna är sådana delar märkta *(inte provat på riktig hårdvara)*.
