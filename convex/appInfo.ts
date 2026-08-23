import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

const platformValidator = v.union(
  v.literal("all"),
  v.literal("win32"),
  v.literal("linux"),
  v.literal("darwin"),
);

export const getForPlatform = query({
  args: { platform: platformValidator },
  handler: async (ctx, args) => {
    const platformRow = await ctx.db
      .query("appInfo")
      .withIndex("by_platform", (q) => q.eq("platform", args.platform))
      .unique();

    if (platformRow) {
      return platformRow;
    }

    return await ctx.db
      .query("appInfo")
      .withIndex("by_platform", (q) => q.eq("platform", "all"))
      .unique();
  },
});

export const getLatest = query({
  args: {},
  handler: async (ctx) => {
    return await ctx.db
      .query("appInfo")
      .withIndex("by_platform", (q) => q.eq("platform", "all"))
      .unique();
  },
});

export const upsert = mutation({
  args: {
    platform: platformValidator,
    version: v.string(),
    minSupportedVersion: v.optional(v.string()),
    releaseNotes: v.optional(v.string()),
    updateFeedUrl: v.optional(v.string()),
    downloadUrl: v.optional(v.string()),
    forceUpdate: v.optional(v.boolean()),
  },
  handler: async (ctx, args) => {
    const existing = await ctx.db
      .query("appInfo")
      .withIndex("by_platform", (q) => q.eq("platform", args.platform))
      .unique();

    const payload = {
      platform: args.platform,
      version: args.version,
      minSupportedVersion: args.minSupportedVersion,
      releaseNotes: args.releaseNotes,
      updateFeedUrl: args.updateFeedUrl,
      downloadUrl: args.downloadUrl,
      forceUpdate: args.forceUpdate ?? false,
      updatedAt: Date.now(),
    };

    if (existing) {
      await ctx.db.patch(existing._id, payload);
      return existing._id;
    }

    return await ctx.db.insert("appInfo", payload);
  },
});

/** Seed default release row for first-time deployments. */
export const seedDefaults = mutation({
  args: {},
  handler: async (ctx) => {
    const existing = await ctx.db
      .query("appInfo")
      .withIndex("by_platform", (q) => q.eq("platform", "all"))
      .unique();

    if (existing) {
      return { seeded: false, id: existing._id };
    }

    const id = await ctx.db.insert("appInfo", {
      platform: "all",
      version: "0.1.0",
      minSupportedVersion: "0.1.0",
      releaseNotes: "Initial ChipVerify Desktop release.",
      updateFeedUrl: "",
      downloadUrl: "",
      forceUpdate: false,
      updatedAt: Date.now(),
    });

    return { seeded: true, id };
  },
});
