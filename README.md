# REA Map Maker for 7 Days to Die

Extract the ZIP and run `Start_Map_Maker.cmd` on Windows. The app uses your installed Steam build V3.4.0 (b10), plus Python 3.13 or later with Tk and Pillow. Keep the files together. Close the game before generating a preview.

1. Check the game folder. Enter the world name and seed. The default world size is 8,192. You can also select 6,144 or 10,240.
2. Add any optional map layers. Leave a layer blank to use the game’s generator for that layer.
3. Select **Generate / Regenerate**. The game runs in a background process using a temporary data folder. The preview shows terrain, roads, water and yellow trader markers. Trader coordinates appear below the map.
4. Change the seed and generate again, or select **Save world to 7 Days to Die**. Save publishes the processed world under `%APPDATA%\7DaysToDie\GeneratedWorlds\<World name>`. Select the world under **New Game**. Existing world folders are protected against overwrite.

| Layer | Input |
| --- | --- |
| Height | Full-size 16-bit unsigned little-endian RAW. The app aligns image-style RAW files with the PNG layers automatically. Keep the original `dtm.raw` or `dtm_processed.raw` name when importing a native game height map. The generator may grade terrain around towns, POIs and roads. |
| Biomes | Use a full-size PNG with the game's five exact colors for mixed biomes. A square single-biome PNG can be any resolution and allows small color shifts. A full-size single-biome PNG can also have a one-pixel export border. The app writes the game's internal biome resolution without blending colors. |
| Water | PNG mask at full world size. Opaque white pixels mark water. All other pixels are dry. The app calculates the water elevation from the terrain and caps the depth at three blocks. |
| Roads | Native `splat3.png`, full world size. Use opaque red and green and transparent black. Town tiles and POI geometry still come from the game. |

The game handles world serialization, processing and checksums. The app validates the files and publishes the entire resulting world folder. The preview process disables Epic cross-platform login and exits after the game creates the world, before the multiplayer server starts accepting connections. The app uses the selected installation’s game configuration and its installed game-folder mods.

Verified with an isolated copy of V3.4.0 (b10) that redirected the game’s profile and crash logs into writable test storage. The test generated 6K worlds with stock and imported layers, built trader previews, checked native file checksums, and reopened a published world by its chosen name. The fixture used the base game and its bundled Harmony mod. The app package includes only the app and bridge, and uses the regular Steam installation when launched.
