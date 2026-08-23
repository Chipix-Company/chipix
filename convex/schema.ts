import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

/**
 * Canonical desktop release metadata for ChipVerify auto-update.
 * One row per platform (win32, linux, darwin) plus optional "all" fallback.
 */
export default defineSchema({
  appInfo: defineTable({
    platform: v.union(
      v.literal("all"),
      v.literal("win32"),
      v.literal("linux"),
      v.literal("darwin"),
    ),
    version: v.string(),
    minSupportedVersion: v.optional(v.string()),
    releaseNotes: v.optional(v.string()),
    /** Base URL for electron-updater generic provider (hosts latest.yml). */
    updateFeedUrl: v.optional(v.string()),
    /** Direct installer URL fallback when generic feed is unavailable. */
    downloadUrl: v.optional(v.string()),
    forceUpdate: v.optional(v.boolean()),
    updatedAt: v.number(),
  }).index("by_platform", ["platform"]),

  licenses: defineTable({
    licenseKey: v.string(),
    customerName: v.optional(v.string()),
    email: v.optional(v.string()),
    plan: v.string(),
    status: v.union(
      v.literal("active"),
      v.literal("revoked"),
      v.literal("expired"),
      v.literal("trial"),
    ),
    expiresAt: v.optional(v.number()),
    dailyTokenLimit: v.optional(v.number()),
    monthlyTokenLimit: v.optional(v.number()),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_license_key", ["licenseKey"])
    .index("by_status", ["status"]),

  devices: defineTable({
    machineId: v.string(),
    licenseId: v.optional(v.id("licenses")),
    status: v.union(
      v.literal("active"),
      v.literal("pending"),
      v.literal("revoked"),
      v.literal("expired"),
    ),
    activationHash: v.optional(v.string()),
    platform: v.optional(v.string()),
    appVersion: v.optional(v.string()),
    lastHeartbeatAt: v.optional(v.number()),
    activatedAt: v.optional(v.number()),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_machine_id", ["machineId"])
    .index("by_license_id", ["licenseId"]),

  remoteConfig: defineTable({
    name: v.string(),
    provider: v.union(
      v.literal("openai"),
      v.literal("bedrock"),
      v.literal("gemini"),
      v.literal("azure_openai"),
      v.literal("nim"),
    ),
    model: v.string(),
    maxTokens: v.number(),
    temperature: v.number(),
    dailyTokenLimit: v.optional(v.number()),
    monthlyTokenLimit: v.optional(v.number()),
    perMachineDailyLimit: v.optional(v.number()),
    allowUnknownDevices: v.optional(v.boolean()),
    gatewayEnabled: v.optional(v.boolean()),
    featureFlags: v.optional(v.any()),
    updatedAt: v.number(),
  }).index("by_name", ["name"]),

  providerSecrets: defineTable({
    provider: v.union(
      v.literal("openai"),
      v.literal("bedrock"),
      v.literal("gemini"),
      v.literal("azure_openai"),
      v.literal("nim"),
    ),
    envKey: v.string(),
    baseUrl: v.optional(v.string()),
    deployment: v.optional(v.string()),
    apiVersion: v.optional(v.string()),
    apiStyle: v.optional(v.string()),
    updatedAt: v.number(),
  }).index("by_provider", ["provider"]),

  usageEvents: defineTable({
    licenseId: v.optional(v.id("licenses")),
    deviceId: v.optional(v.id("devices")),
    machineId: v.string(),
    provider: v.string(),
    model: v.string(),
    source: v.string(),
    projectId: v.optional(v.string()),
    threadId: v.optional(v.string()),
    inputTokens: v.number(),
    outputTokens: v.number(),
    totalTokens: v.number(),
    success: v.boolean(),
    errorClass: v.optional(v.string()),
    createdAt: v.number(),
    metadata: v.optional(v.any()),
  })
    .index("by_machine_time", ["machineId", "createdAt"])
    .index("by_license_time", ["licenseId", "createdAt"]),

  quotaWindows: defineTable({
    scopeType: v.union(v.literal("license"), v.literal("machine")),
    scopeId: v.string(),
    window: v.union(v.literal("daily"), v.literal("monthly")),
    windowStart: v.number(),
    totalTokens: v.number(),
    updatedAt: v.number(),
  }).index("by_scope_window", ["scopeType", "scopeId", "window", "windowStart"]),

  desktopReleases: defineTable({
    platform: v.union(
      v.literal("all"),
      v.literal("win32"),
      v.literal("linux"),
      v.literal("darwin"),
    ),
    version: v.string(),
    minSupportedVersion: v.optional(v.string()),
    releaseNotes: v.optional(v.string()),
    updateFeedStorageId: v.optional(v.id("_storage")),
    installerStorageId: v.optional(v.id("_storage")),
    updateFeedUrl: v.optional(v.string()),
    downloadUrl: v.optional(v.string()),
    forceUpdate: v.optional(v.boolean()),
    publishedAt: v.optional(v.number()),
    updatedAt: v.number(),
  }).index("by_platform", ["platform"]),
});
