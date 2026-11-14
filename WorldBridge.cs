using System;
using System.IO;
using HarmonyLib;
using UnityEngine;
using WorldGenerationEngineFinal;
using Path = System.IO.Path;

public sealed class WorldBridge : IModApi
{
    private const int MaxWaterDepth = 3;
    private const int MaxEncodedWaterElevation = 249;
    private const string WaterMaskFile = "water.mask";
    private static string job;

    public void InitMod(Mod mod)
    {
        job = Environment.GetEnvironmentVariable("REA_MAP_MAKER_JOB");
        if (string.IsNullOrEmpty(job) ||
            !GameIO.PathsEquals(job, GameIO.GetDeviceLocalUserGameDataDir(), true))
            return;

        try
        {
            var harmony = new Harmony("local.reamapmaker.worldbridge");
            Patch(harmony, "GenerateTerrain", nameof(AfterTerrain));
            Patch(harmony, "GenerateTerrainLast", nameof(AfterFeatures));
            Patch(harmony, "DrawRoads", nameof(AfterRoads), typeof(Color32[]));
            Patch(harmony, "FinalizeWater", nameof(AfterWater));
            harmony.Patch(AccessTools.DeclaredMethod(typeof(WorldDecoratorPOIFromImage), "InitData"),
                prefix: new HarmonyMethod(typeof(WorldBridge), nameof(BeforeWaterProcessing)));
            ModEvents.CreateWorldDone.RegisterHandler(OnWorldCreated);
            File.WriteAllText(Path.Combine(job, "bridge.ready"), "ready");
        }
        catch (Exception ex)
        {
            File.WriteAllText(Path.Combine(job, "bridge.error"), ex.ToString());
            throw;
        }
    }

    private static void Patch(Harmony harmony, string method, string postfix, params Type[] arguments)
    {
        var original = AccessTools.DeclaredMethod(typeof(WorldBuilder), method, arguments);
        if (original == null)
            throw new MissingMethodException(typeof(WorldBuilder).FullName, method);
        harmony.Patch(original, postfix: new HarmonyMethod(typeof(WorldBridge), postfix));
    }

    private static void AfterTerrain(WorldBuilder __instance)
    {
        ReadRows("height.raw", __instance.WorldSize, 2, (row, y) => ApplyHeight(__instance, row, y));
        var biomes = __instance.biomeDest;
        ReadRows("biomes.rgba", __instance.BiomeSize, 4, (row, y) => ApplyColors(biomes, row, y));
        ApplyWaterMask(__instance);
    }

    private static void AfterFeatures(WorldBuilder __instance)
    {
        ReadRows("height.raw", __instance.WorldSize, 2, (row, y) => ApplyHeight(__instance, row, y));
        ApplyWaterMask(__instance);
    }

    private static void AfterRoads(WorldBuilder __instance, Color32[] __0)
    {
        ReadRows("roads.rgba", __instance.WorldSize, 4, (row, y) => ApplyColors(__0, row, y));
    }

    private static void AfterWater(WorldBuilder __instance)
    {
        if (ApplyWaterMask(__instance))
            return;

        var water = __instance.data.waterDest;
        var height = __instance.data.HeightMap;
        for (int i = 0; i < water.Length; i++)
        {
            if (water[i] > 0)
            {
                water[i] = Math.Min((byte)water[i], WaterElevationForGround(SerializedGround(height[i])));
            }
        }
    }

    private static void BeforeWaterProcessing(WorldDecoratorPOIFromImage __instance)
    {
        var texture = __instance.splat4Tex;
        if (texture == null || __instance.heightMap == null)
            return;

        // Native processing can change terrain under prefabs before it reads the water texture.
        bool hasWaterMask = File.Exists(Path.Combine(job, WaterMaskFile));
        var pixels = texture.GetPixelData<TextureUtils.ColorARGB32>(0);
        for (int i = 0; i < pixels.Length; i++)
        {
            var pixel = pixels[i];
            if (pixel.b == 0)
                continue;
            byte shallowElevation = WaterElevationForGround(__instance.heightMap.GetAt(i));
            pixel.b = hasWaterMask ? shallowElevation : Math.Min(pixel.b, shallowElevation);
            pixels[i] = pixel;
        }
    }

    private static float SerializedGround(float builderHeight)
    {
        // Match the elevation written to dtm.raw, including its one-block offset.
        return Mathf.Clamp((int)((builderHeight - 1f) * 257f), 0, ushort.MaxValue) / 257f;
    }

    private static byte WaterElevationForGround(float ground)
    {
        // The blue channel is one below the highest filled block, and filling includes both endpoints.
        int elevation = Mathf.FloorToInt(ground) + MaxWaterDepth - 2;
        // The game reserves decoration ID 255 and adds 5 to the encoded elevation.
        return (byte)Mathf.Clamp(elevation, 1, MaxEncodedWaterElevation);
    }

    private static void ApplyHeight(WorldBuilder world, byte[] row, int y)
    {
        for (int x = 0; x < world.WorldSize; x++)
        {
            int pixel = x * 2;
            // Leave a fraction of one RAW unit so the game's truncation preserves the sample.
            float sample = row[pixel] + 256 * row[pixel + 1] + 0.125f;
            world.data.HeightMap[x + y * world.WorldSize] =
                sample / 257f + 1f;
        }
    }

    private static bool ApplyWaterMask(WorldBuilder world)
    {
        return ReadRows(WaterMaskFile, world.WorldSize, 1, (row, y) =>
        {
            int start = y * world.WorldSize;
            for (int x = 0; x < row.Length; x++)
            {
                int index = start + x;
                world.data.waterDest[index] = row[x] == 0
                    ? 0
                    : WaterElevationForGround(SerializedGround(world.data.HeightMap[index]));
            }
        });
    }

    private static void ApplyColors(Color32[] map, byte[] row, int y)
    {
        int width = row.Length / 4;
        for (int x = 0; x < width; x++)
        {
            int i = x * 4;
            map[x + y * width] = new Color32(row[i], row[i + 1], row[i + 2], row[i + 3]);
        }
    }

    private static bool ReadRows(string file, int size, int channels, Action<byte[], int> apply)
    {
        string path = Path.Combine(job, file);
        if (!File.Exists(path))
            return false;
        try
        {
            using (var reader = new BinaryReader(File.OpenRead(path)))
            {
                if (reader.BaseStream.Length != (long)size * size * channels)
                    throw new InvalidDataException("Unexpected layer size: " + file);
                for (int y = 0; y < size; y++)
                    apply(reader.ReadBytes(size * channels), y);
            }
            return true;
        }
        catch (Exception ex)
        {
            File.WriteAllText(Path.Combine(job, "bridge.error"), ex.ToString());
            throw;
        }
    }

    private static void OnWorldCreated(ref ModEvents.SCreateWorldDoneData data)
    {
        File.WriteAllText(Path.Combine(job, "world.ready"), GamePrefs.GetString(EnumGamePrefs.GameWorld));
        Application.Quit();
    }
}
