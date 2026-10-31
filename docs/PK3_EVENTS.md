# Gen 3 Pokémon in Project Pokémon's gallery, and how each was given out

Stage 9's first step (31 October 2026). The goal (docs/ROADMAP.md) asks for each file to go by
the route it originally came by, wherever that route leaves the game to save it itself. This page
catalogues the gallery's Gen 3 `.pk3` files by event and records, with sources, how each event
reached the games. The files themselves stay on the user's PC.

**How it was made.** `duo_events.py pk3-info` reads each file (all 3,344 are stored in PKHeX's
decrypted form, and every checksum is good). The gallery's folders name the events; the trainer
names, IDs, origin games and met data below come from the files. The methods come from the
sources at the end; "likely" means no source says so outright.

## The answer in one table

| Original route | Files |
|---|---|
| **Trade** | 435 |
| **Multiboot program** (writes the save) | 505 |
| **Event machine** (writes the save) | 2192 |
| **GameCube software** (writes the save) | 28 |
| Unknown | 17 |
| Never distributed | 167 |

So **one event, the Mystery Mew, was a trade**: about 430 files. Nearly everything else that was
released reached the games by a route that **wrote the save directly** - a multiboot program
(the player's GBA boots holding Start and Select, and the Pokémon lands in the party), an event
machine that "injected" it, or GameCube software over the GameCube-GBA cable. About 150 files
were never distributed at all.

## What that means for the goal (the user's decision)

- **The Mystery Mew:** a trade distributor is its authentic route. The DUO plays the other player
  in the Cable Club and the game saves the Mew itself.
- **Everything that wrote the save:** the authentic route conflicts with the rule that our code
  never writes cartridge saves. Two ways forward:
  1. **The nearest route that leaves the game in charge: a trade**, labelled as such. One trade
     distributor then covers every released event.
  2. **The original route**: a multiboot program sent from the DUO's GBA that writes the
     Pokémon into the save, as the distribution cartridges did. Authentic, but it means our code
     writing saves, with the risks that brings.
- **Never distributed:** no original route exists. Leave them out, or offer the trade route for
  them too, clearly marked as never distributed.
- **Unknown:** a few events need more research (below).

## Every event

| Event | Files | Pokémon (most common) | Trainer names | Original route | Notes |
|---|---|---|---|---|---|
| PCNY campaigns (Gen 3), New York | 818 | SEVIPER ×48, GLOOM ×44, SHIFTRY ×43 … | PCNYd ×456, PCNYc ×307, PCNYb ×55 | **Event machine** (writes the save) (source) | made by an event machine and injected into the save [pp-pcny] |
| Mystery Mew (MYSTRY), Toys "R" Us, US 2006; also 9 others from the same distribution cartridge | 435 | MEW ×426, GENGAR, MACHAMP … | MYSTRY ×426, JEREMY ×9 | **Trade** (source) | link trade from a FireRed distribution cartridge [bp-device]; the gallery holds that cartridge's save |
| PokéPark 2005 (ポケパーク), Japan | 409 | PICHU ×48, MINUN ×38, WHISMUR ×33 … | ポケパーク ×397, ???? ×12 | **Event machine** (writes the save) (likely) | Japanese event distribution machine |
| Golden Week (ＧＷ), Japan | 318 | PIKACHU | ?? | **Event machine** (writes the save) (likely) | Japanese event distribution machine |
| Pokémon Center Yokohama (ヨコハマ), Japan | 264 | PIKACHU | ヨコハマ | **Event machine** (writes the save) (likely) | Japanese event distribution machine |
| Shiny Zigzagoon (RUBY / SAPHIRE), with the Berry Fix, US/Europe 2004-2007 | 186 | ZIGZAGOON | RUBY ×96, SAPHIRE ×90 | **Multiboot program** (writes the save) (source) | given with the save-repair program [serebii-263] |
| 10th Anniversary (10 ANIV), US 2006 | 153 | DEOXYS ×31, CELEBI ×18, ARTICUNO ×7 … | 10 ANIV ×112, SPACE C ×31, 10ANNIV ×10 | **Multiboot program** (writes the save) (likely) | distribution cartridge of the Aura Mew family [digiex-10aniv] |
| Pokémon Battle Tour 2004 eggs, Japan | 118 | ODDISH ×33, MEOWTH ×32, POLIWAG ×29 … | あひる | **Event machine** (writes the save) (likely) | Japanese event distribution machine |
| Unreleased and debug Pokémon (Japanese) | 115 | DODRIO ×2, JIGGLYPUFF ×2, POLIWAG ×2 … | デバッグポケ1 ×92, フクシ?? ×6, えつこ ×6 … | Never distributed (source) | never distributed |
| 5th Anniversary eggs, Japan | 89 | PICHU ×28, BAGON ×28, ABSOL ×22 … | オヤ???? | **Event machine** (writes the save) (likely) | Japanese event distribution machine |
| Wishing Star Jirachi (ネガイボシ), Japan 2003 | 78 | JIRACHI | ネガイボシ | **Event machine** (writes the save) (source) | the Pokémon Distribution Machine, updated for Gen 3 [bp-device]; how it reached the game isn't stated |
| Aura Mew (ハドウ), Japan | 71 | MEW ×69, REGISTEEL, REGICE | ハドウ | **Multiboot program** (writes the save) (likely) | as the western Aura Mew |
| Forest Celebi (ミツリン), Japan | 43 | CELEBI | ミツリン | **Event machine** (writes the save) (likely) | Japanese event distribution machine |
| Aura Mew, Germany | 25 | MEW | Aura | **Multiboot program** (writes the save) (source) | multiboot: the player's GBA boots holding Start and Select; the Mew lands in the party [digiex-aura] |
| Shiny Zigzagoon (ルビー / サファイア), with the Repair Program, Japan 2003-2006 | 24 | ZIGZAGOON | サファイア ×15, ルビー ×9 | **Multiboot program** (writes the save) (source) | given with the save-repair program [serebii-263] |
| ANA (airline) campaign, Japan | 23 | PIKACHU | ??? | **Event machine** (writes the save) (likely) | Japanese event distribution machine |
| "Gather More Pokémon!" campaign (unreleased), Japan | 14 | SNUBBULL, MAREEP, SUNKERN … | サッポロ | Never distributed (source) | never distributed |
| Wishmaker Jirachi (WISHMKR), US bonus disc | 13 | JIRACHI | WISHMKR | **GameCube software** (writes the save) (source) | Colosseum bonus disc over the GameCube-GBA cable [bp-game][pm-update] |
| Unreleased and debug Pokémon (English) | 13 | RAICHU, GARDEVOIR, PIKACHU … | ??????1 ×3, HaxAras ×3, AO ×2 … | Never distributed (source) | never distributed |
| Pokémon Festa 2005 (フェスタ), Japan | 11 | METANG | フェスタ | **Event machine** (writes the save) (likely) | Japanese event distribution machine |
| Top 10 (10ANIV), Spain 2006 | 10 | PIKACHU, RAIKOU, SUICUNE … | 10ANIV | **Multiboot program** (writes the save) (likely) | the Top 10 distribution system [bp-device]; the Aura Mew family |
| Top 10 (10JAHRE), Germany 2006 | 10 | SUICUNE, LUGIA, PIKACHU … | 10JAHRE | **Multiboot program** (writes the save) (likely) | as above |
| Top 10 (10ANNI), Italy 2006 | 10 | LATIAS, LUGIA, CHARIZARD … | 10ANNI | **Multiboot program** (writes the save) (likely) | as above |
| Top 10 (10ANNIV), France 2006 | 10 | RAIKOU, PIKACHU, LUGIA … | 10ANNIV | **Multiboot program** (writes the save) (likely) | as above |
| Tanabata Jirachi 2006, Japan | 8 | JIRACHI | タナバタ | **Event machine** (writes the save) (likely) | Japanese event distribution machine |
| Unreleased and debug Pokémon (Spanish) | 8 | LUGIA, DEOXYS, MILOTIC … | KAORI ×5, Debug-S ×2, Valeron | Never distributed (source) | never distributed |
| Unreleased and debug Pokémon (Italian) | 8 | CELEBI, PIKACHU, MEWTWO … | Debug-I ×2, KITAMUu ×2, SOFO … | Never distributed (source) | never distributed |
| Shōnen Sunday Wobbuffet (サンデー), Japan | 6 | WOBBUFFET | サンデー | **Event machine** (writes the save) (likely) | Japanese event distribution machine |
| Pokémon Rocks America (ROCKS), US 2005 | 6 | METANG | ROCKS | Unknown (unknown) | not found |
| DOEL Deoxys | 5 | DEOXYS | DOEL | Unknown (unknown) | not found |
| Tanabata Jirachi 2005, Japan | 4 | JIRACHI | タナバタ | **Event machine** (writes the save) (likely) | Japanese event distribution machine |
| Pokémon Box bonus eggs | 4 | SWABLU, SKITTY, ZIGZAGOON … | ????? | **GameCube software** (writes the save) (source) | Pokémon Box Ruby & Sapphire over the GameCube-GBA cable [bp-game] |
| Wish eggs, PCNY | 4 | FARFETCH'D, KANGASKHAN, DROWZEE … | ??? ×3, ????? | Unknown (unknown) | Bulbapedia: the PCNY "Gotta Catch 'Em All Station", "wireless Mystery Gift" [bp-device]; Gen 3 Mystery Gift can't carry a Pokémon, so this needs checking |
| Mystery Mew (unreleased variants) | 4 | MEW | MYSTRY | Never distributed (source) | never distributed |
| Colosseum Pokémon (e-Reader), Japan | 3 | SCIZOR, MAREEP, TOGEPI | ???? | **GameCube software** (writes the save) (likely) | their origin is Colosseum/XD |
| Tanabata Jirachi 2004, Japan | 3 | JIRACHI | タナバタ | **Event machine** (writes the save) (likely) | Japanese event distribution machine |
| Aura Mew, US/UK | 3 | MEW | Aura | **Multiboot program** (writes the save) (source) | multiboot [digiex-aura] |
| Unreleased and debug Pokémon (German) | 3 | MILOTIC, EEVEE, GROUDON | ???????, Debug-G, CRYST?L | Never distributed (source) | never distributed |
| Pokémon Channel Jirachi, Spain | 2 | JIRACHI | CHANNEL | **GameCube software** (writes the save) (source) | Pokémon Channel (GameCube) [bp-game] |
| Colosseum Pikachu (コロシアム) and Battle Mountain (バトルやま), Japan | 2 | PIKACHU, HO-OH | コロシアム, バトルやま | **GameCube software** (writes the save) (likely) | Colosseum bonus disc for Pikachu [bp-game]; Battle Mountain unknown |
| Ageto Celebi (アゲト), Japanese bonus disc | 2 | CELEBI | アゲト | **GameCube software** (writes the save) (source) | Colosseum bonus disc [bp-game] |
| Pokémon Channel Jirachi, Europe/Australia | 2 | JIRACHI | CHANNEL | **GameCube software** (writes the save) (source) | Pokémon Channel (GameCube) [bp-game] |
| Unreleased and debug Pokémon (French) | 2 | ELECTRIKE, GROUDON | Debug-F, CHAR! | Never distributed (source) | never distributed |
| Aura Mew, Spain | 1 | MEW | Aura | **Multiboot program** (writes the save) (source) | multiboot [digiex-aura] |
| MONTE L, Italy | 1 | HO-OH | MONTE L | Unknown (unknown) | not found |
| Aura Mew, Italy | 1 | MEW | Aura | **Multiboot program** (writes the save) (source) | multiboot [digiex-aura] |
| Aura Mew, France | 1 | MEW | Aura | **Multiboot program** (writes the save) (source) | multiboot [digiex-aura] |
| MATTLE, English | 1 | HO-OH | MATTLE | Unknown (unknown) | not found |

## Still to find out

- **Wish eggs:** Bulbapedia says PCNY's later station gave them by "wireless Mystery Gift", but
  Gen 3 Mystery Gift can't carry a Pokémon. Probably the station's own wireless multiboot; needs
  a source.
- **The Japanese event machines:** that Japan used a distribution machine is sourced; how it
  reached the game (injection, as PCNY's) is assumed.
- **10 ANIV and the Top 10:** taken to be the Aura Mew family of multiboot cartridges, since the
  community's distribution ROMs come from that family; worth confirming.
- **ROCKS, DOEL, MONTE L, MATTLE, Battle Mountain:** not yet found.
- **The 9 non-Mew Pokémon in the Toys "R" Us folder** (trainer JEREMY): probably the
  distribution cartridge's own party, not an event.

## Sources

- [bp-device] Bulbapedia, [Distribution device](https://bulbapedia.bulbagarden.net/wiki/Distribution_device): the Gen 3 update of the Pokémon Distribution Machine (Wishing Star Jirachi); "Mystery Mew link trade distribution"; the Top 10 distribution system; the PCNY "Gotta Catch 'Em All Station".
- [bp-game] Bulbapedia, [List of game-based Pokémon distributions in Generation III](https://bulbapedia.bulbagarden.net/wiki/List_of_game-based_Pok%C3%A9mon_distributions_in_Generation_III): Channel Jirachi, Wishmaker Jirachi, Ageto Celebi, Colosseum Pikachu, Pokémon Box eggs.
- [pp-pcny] Project Pokémon, [PCNY Gotta Catch 'Em All Station](https://projectpokemon.org/home/forums/topic/55380-pcny-gotta-catch-em-all-station/page/2): "Gen1-3 events were created by an external process (event machine/device) then injected to the recipient's savegame"; and [PCNY distribution machine savefiles](https://projectpokemon.org/home/files/file/4073-pcny-distribution-machine-savefiles/).
- [digiex-aura] Digiex, [Aura Mew distribution cartridge](https://digiex.net/threads/pokemon-aura-mew-distribution-cartridge-cartridge-gba-download-eng-fre-ger-ita-spa.15296/): multiboot, Start and Select at power-on, the Mew lands in the party.
- [digiex-10aniv] Digiex, [10ANNIV distribution cartridge](https://digiex.net/threads/pokemon-10anniv-distribution-cartridge-gba-download-eng-fre-ger-ita-spa.14897/), and [all Gen 3 distributions recreated](https://digiex.net/threads/dreams-come-true-all-gen-3-pokemon-gba-distributions-recreated.16511/) (built from the Aura Mew ROM).
- [serebii-263] Serebii, [Zigzagoon events](https://www.serebii.net/events/dex/263.shtml): "Berry Fix" (US/Europe, from 17 March 2004) and "Repair Program" (Japan, from 7 November 2003).
- [pm-update] PocketMonsters.net, [Ruby/Sapphire update](https://www.pocketmonsters.net/index.php/content/Ruby_Sapphire_Update): the bonus disc, Colosseum and Pokémon Box over the GameCube-GBA cable, and the multiboot from FireRed/LeafGreen/Emerald.
