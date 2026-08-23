import { action, mutation, query } from "./_generated/server";
import { v } from "convex/values";
import { api } from "./_generated/api";

const platformValidator = v.union(
  v.literal("all"),
  v.literal("win32"),
  v.literal("linux"),
  v.literal("darwin"),
);

const providerValidator = v.union(
  v.literal("openai"),
  v.literal("bedrock"),
  v.literal("gemini"),
  v.literal("azure_openai"),
  v.literal("nim"),
);

const DEFAULT_CONFIG = {
  name: "default",
  provider: "bedrock" as const,
  model: "deepseek.v3.2",
  maxTokens: 16384,
  temperature: 0.3,
  dailyTokenLimit: 250_000,
  monthlyTokenLimit: 2_500_000,
  perMachineDailyLimit: 100_000,
  allowUnknownDevices: false,
  gatewayEnabled: true,
  featureFlags: {},
};

function now() {
  return Date.now();
}

function normalizeMachineId(machineId: string) {
  return String(machineId || "").trim().toUpperCase();
}

function withoutUndefined<T extends Record<string, any>>(value: T): any {
  return Object.fromEntries(
    Object.entries(value).filter(([, item]) => item !== undefined),
  );
}

function dayStart(timestamp: number) {
  const date = new Date(timestamp);
  date.setUTCHours(0, 0, 0, 0);
  return date.getTime();
}

function monthStart(timestamp: number) {
  const date = new Date(timestamp);
  date.setUTCDate(1);
  date.setUTCHours(0, 0, 0, 0);
  return date.getTime();
}

function tokenEstimateFromMessages(messages: unknown) {
  try {
    const text = JSON.stringify(messages || "");
    return Math.max(1, Math.ceil(text.length / 4));
  } catch {
    return 1;
  }
}

function parseUsage(rawUsage: any, provider: string, model: string, fallbackInput: number, fallbackOutputText: string) {
  const inputTokens = Number(
    rawUsage?.prompt_tokens
      ?? rawUsage?.input_tokens
      ?? rawUsage?.promptTokenCount
      ?? rawUsage?.prompt_token_count
      ?? fallbackInput
      ?? 0,
  );
  const outputTokens = Number(
    rawUsage?.completion_tokens
      ?? rawUsage?.output_tokens
      ?? rawUsage?.candidatesTokenCount
      ?? rawUsage?.candidates_token_count
      ?? Math.max(1, Math.ceil(String(fallbackOutputText || "").length / 4)),
  );
  const totalTokens = Number(
    rawUsage?.total_tokens
      ?? rawUsage?.totalTokenCount
      ?? rawUsage?.total_token_count
      ?? inputTokens + outputTokens,
  );
  return {
    inputTokens: Math.max(0, inputTokens || 0),
    outputTokens: Math.max(0, outputTokens || 0),
    totalTokens: Math.max(0, totalTokens || inputTokens + outputTokens),
    provider,
    model,
  };
}

function normalizeOpenAiMessages(args: any) {
  if (Array.isArray(args.messages) && args.messages.length > 0) {
    return args.messages;
  }
  const messages = [];
  if (args.systemPrompt) {
    messages.push({ role: "system", content: String(args.systemPrompt) });
  }
  messages.push({ role: "user", content: String(args.userMessage || args.prompt || "") });
  return messages;
}

async function callOpenAiCompatible(args: any, access: any, apiKey: string) {
  const provider = access.config.provider;
  const model = String(args.model || access.config.model || DEFAULT_CONFIG.model);
  const maxTokens = Number(args.maxTokens || access.config.maxTokens || DEFAULT_CONFIG.maxTokens);
  const temperature = Number(args.temperature ?? access.config.temperature ?? DEFAULT_CONFIG.temperature);
  const secret = access.providerSecret || {};
  const isAzure = provider === "azure_openai";
  const baseUrl = String(
    secret.baseUrl
      || (isAzure ? process.env.AZURE_OPENAI_ENDPOINT : "")
      || (provider === "bedrock"
        ? process.env.BEDROCK_API_BASE
          || `https://bedrock-mantle.${process.env.BEDROCK_REGION || "us-east-1"}.api.aws/v1`
        : "")
      || (provider === "nim" ? process.env.NIM_API_BASE : "")
      || "https://api.openai.com/v1",
  ).replace(/\/+$/, "");
  const apiVersion = String(secret.apiVersion || process.env.AZURE_OPENAI_API_VERSION || "2024-10-21");
  const deployment = String(secret.deployment || model);
  const url = isAzure
    ? `${baseUrl.replace(/\/openai(?:\/v1)?$/i, "")}/openai/deployments/${encodeURIComponent(deployment)}/chat/completions?api-version=${encodeURIComponent(apiVersion)}`
    : `${baseUrl}/chat/completions`;

  const messages = normalizeOpenAiMessages(args);
  const payload: any = {
    model,
    messages,
  };
  if (Array.isArray(args.tools) && args.tools.length > 0) {
    payload.tools = args.tools;
    payload.tool_choice = args.toolChoice || args.tool_choice || "auto";
  }
  if (String(model).toLowerCase().startsWith("gpt-5") && provider === "openai") {
    payload.max_completion_tokens = maxTokens;
  } else {
    payload.max_tokens = maxTokens;
    payload.temperature = temperature;
  }
  if (args.extraBody && typeof args.extraBody === "object") {
    Object.assign(payload, args.extraBody);
  }

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(isAzure ? { "api-key": apiKey } : { Authorization: `Bearer ${apiKey}` }),
    },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = body?.error?.message || body?.message || `Provider request failed (${response.status})`;
    const error: any = new Error(message);
    error.status = response.status;
    error.body = body;
    throw error;
  }

  const choice = body?.choices?.[0] || {};
  const message = choice?.message || {};
  const toolCalls = Array.isArray(message.tool_calls)
    ? message.tool_calls.map((toolCall: any) => ({
      id: String(toolCall.id || ""),
      name: String(toolCall.function?.name || ""),
      arguments: (() => {
        try {
          return JSON.parse(toolCall.function?.arguments || "{}");
        } catch {
          return {};
        }
      })(),
    }))
    : [];
  const content = String(message.content || "");
  return {
    content,
    reasoning: String(message.reasoning_content || ""),
    toolCalls,
    finishReason: String(choice.finish_reason || "stop"),
    usage: parseUsage(body.usage, provider, model, tokenEstimateFromMessages(messages), content),
    provider,
    model,
  };
}

async function callGemini(args: any, access: any, apiKey: string) {
  const model = String(args.model || access.config.model || "gemini-2.5-pro").replace(/^models\//, "");
  const maxTokens = Number(args.maxTokens || access.config.maxTokens || DEFAULT_CONFIG.maxTokens);
  const temperature = Number(args.temperature ?? access.config.temperature ?? DEFAULT_CONFIG.temperature);
  const systemPrompt = String(args.systemPrompt || "");
  const userMessage = String(
    args.userMessage
      || args.prompt
      || (Array.isArray(args.messages) ? args.messages.map((m: any) => m.content || "").join("\n") : ""),
  );

  const payload: any = {
    contents: [{ role: "user", parts: [{ text: userMessage }] }],
    generationConfig: {
      temperature,
      maxOutputTokens: maxTokens,
    },
  };
  if (systemPrompt) {
    payload.systemInstruction = { parts: [{ text: systemPrompt }] };
  }

  const response = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(apiKey)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = body?.error?.message || body?.message || `Gemini request failed (${response.status})`;
    const error: any = new Error(message);
    error.status = response.status;
    error.body = body;
    throw error;
  }

  const content = String(
    body?.candidates?.[0]?.content?.parts
      ?.map((part: any) => part.text || "")
      .join("")
      || "",
  );
  return {
    content,
    reasoning: "",
    toolCalls: [],
    finishReason: "stop",
    usage: parseUsage(body.usageMetadata, "gemini", model, tokenEstimateFromMessages(userMessage), content),
    provider: "gemini",
    model,
  };
}

export const getDefaultRemoteConfig = query({
  args: {},
  handler: async (ctx) => {
    const row = await ctx.db
      .query("remoteConfig")
      .withIndex("by_name", (q) => q.eq("name", "default"))
      .unique();
    return row || { ...DEFAULT_CONFIG, updatedAt: 0 };
  },
});

export const getDesktopVersion = query({
  args: { platform: platformValidator },
  handler: async (ctx, args) => {
    const platformRow = await ctx.db
      .query("desktopReleases")
      .withIndex("by_platform", (q) => q.eq("platform", args.platform))
      .unique();
    const allRow = platformRow
      || await ctx.db.query("desktopReleases").withIndex("by_platform", (q) => q.eq("platform", "all")).unique();
    if (allRow) {
      return allRow;
    }
    return await ctx.db.query("appInfo").withIndex("by_platform", (q) => q.eq("platform", args.platform)).unique()
      || await ctx.db.query("appInfo").withIndex("by_platform", (q) => q.eq("platform", "all")).unique();
  },
});

export const getEntitlement = query({
  args: {
    machineId: v.string(),
    platform: v.optional(v.string()),
    appVersion: v.optional(v.string()),
    activationHash: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const machineId = normalizeMachineId(args.machineId);
    const config = await ctx.db
      .query("remoteConfig")
      .withIndex("by_name", (q) => q.eq("name", "default"))
      .unique() || { ...DEFAULT_CONFIG, updatedAt: 0 };
    const device = await ctx.db
      .query("devices")
      .withIndex("by_machine_id", (q) => q.eq("machineId", machineId))
      .unique();
    const license = device?.licenseId ? await ctx.db.get(device.licenseId) : null;
    const release = await ctx.db
      .query("desktopReleases")
      .withIndex("by_platform", (q) => q.eq("platform", (args.platform as any) || "all"))
      .unique()
      || await ctx.db.query("desktopReleases").withIndex("by_platform", (q) => q.eq("platform", "all")).unique();

    let allowed = Boolean(config.allowUnknownDevices);
    let status = allowed ? "trial" : "unknown_device";
    const timestamp = now();

    if (device) {
      allowed = device.status === "active" || device.status === "pending";
      status = device.status;
    }
    if (license) {
      if (license.status === "revoked") {
        allowed = false;
        status = "revoked";
      } else if (license.expiresAt && license.expiresAt < timestamp) {
        allowed = false;
        status = "expired";
      } else if (license.status === "active" || license.status === "trial") {
        allowed = device ? allowed : true;
        status = license.status;
      }
    }

    const dailyLimit = license?.dailyTokenLimit ?? config.dailyTokenLimit ?? DEFAULT_CONFIG.dailyTokenLimit;
    const monthlyLimit = license?.monthlyTokenLimit ?? config.monthlyTokenLimit ?? DEFAULT_CONFIG.monthlyTokenLimit;

    return {
      allowed,
      status,
      machineId,
      deviceId: device?._id ?? null,
      licenseId: license?._id ?? null,
      plan: license?.plan ?? (allowed ? "trial" : "none"),
      runtimeConfig: {
        provider: config.provider,
        model: config.model,
        maxTokens: config.maxTokens,
        temperature: config.temperature,
        gatewayEnabled: config.gatewayEnabled ?? true,
        featureFlags: config.featureFlags ?? {},
      },
      quota: {
        dailyTokenLimit: dailyLimit,
        monthlyTokenLimit: monthlyLimit,
        perMachineDailyLimit: config.perMachineDailyLimit ?? null,
      },
      release: release
        ? {
          platform: release.platform,
          version: release.version,
          minSupportedVersion: release.minSupportedVersion ?? release.version,
          releaseNotes: release.releaseNotes ?? "",
          updateFeedUrl: release.updateFeedUrl ?? "",
          downloadUrl: release.downloadUrl ?? "",
          forceUpdate: Boolean(release.forceUpdate),
          updatedAt: release.updatedAt,
        }
        : null,
      updatedAt: timestamp,
    };
  },
});

export const recordHeartbeat = mutation({
  args: {
    machineId: v.string(),
    platform: v.optional(v.string()),
    appVersion: v.optional(v.string()),
    activationHash: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const machineId = normalizeMachineId(args.machineId);
    const existing = await ctx.db
      .query("devices")
      .withIndex("by_machine_id", (q) => q.eq("machineId", machineId))
      .unique();
    const payload = {
      machineId,
      platform: args.platform,
      appVersion: args.appVersion,
      activationHash: args.activationHash,
      lastHeartbeatAt: now(),
      updatedAt: now(),
    };
    if (existing) {
      await ctx.db.patch(existing._id, withoutUndefined(payload));
      return existing._id;
    }
    return await ctx.db.insert("devices", {
      ...withoutUndefined(payload),
      status: "pending",
      createdAt: now(),
    });
  },
});

export const getGatewayAccess = query({
  args: {
    machineId: v.string(),
    requestedTokens: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const machineId = normalizeMachineId(args.machineId);
    const config = await ctx.db
      .query("remoteConfig")
      .withIndex("by_name", (q) => q.eq("name", "default"))
      .unique() || { ...DEFAULT_CONFIG, updatedAt: 0 };
    const device = await ctx.db
      .query("devices")
      .withIndex("by_machine_id", (q) => q.eq("machineId", machineId))
      .unique();
    const license = device?.licenseId ? await ctx.db.get(device.licenseId) : null;

    let allowed = Boolean(config.allowUnknownDevices);
    let status = allowed ? "trial" : "unknown_device";
    const timestamp = now();
    if (device) {
      allowed = device.status === "active" || device.status === "pending";
      status = device.status;
    }
    if (license) {
      if (license.status === "revoked") {
        allowed = false;
        status = "revoked";
      } else if (license.expiresAt && license.expiresAt < timestamp) {
        allowed = false;
        status = "expired";
      } else if (license.status === "active" || license.status === "trial") {
        allowed = device ? allowed : true;
        status = license.status;
      }
    }

    const entitlement = {
      allowed,
      status,
      machineId,
      deviceId: device?._id ?? null,
      licenseId: license?._id ?? null,
      plan: license?.plan ?? (allowed ? "trial" : "none"),
      quota: {
        dailyTokenLimit: license?.dailyTokenLimit ?? config.dailyTokenLimit ?? DEFAULT_CONFIG.dailyTokenLimit,
        monthlyTokenLimit: license?.monthlyTokenLimit ?? config.monthlyTokenLimit ?? DEFAULT_CONFIG.monthlyTokenLimit,
        perMachineDailyLimit: config.perMachineDailyLimit ?? null,
      },
    };
    if (!entitlement.allowed) {
      return { allowed: false, reason: entitlement.status, entitlement };
    }
    if (config.gatewayEnabled === false) {
      return { allowed: false, reason: "LLM gateway is disabled", entitlement, config };
    }
    const providerSecret = await ctx.db
      .query("providerSecrets")
      .withIndex("by_provider", (q) => q.eq("provider", config.provider))
      .unique();
    if (!providerSecret) {
      return { allowed: false, reason: `missing provider secret for ${config.provider}`, entitlement, config };
    }

    const day = dayStart(timestamp);
    const month = monthStart(timestamp);
    const dailyMachineWindow = await ctx.db.query("quotaWindows")
      .withIndex("by_scope_window", (q) => q.eq("scopeType", "machine").eq("scopeId", machineId).eq("window", "daily").eq("windowStart", day))
      .unique();
    const licenseScopeId = entitlement.licenseId ? String(entitlement.licenseId) : "";
    const dailyLicenseWindow = licenseScopeId
      ? await ctx.db.query("quotaWindows")
        .withIndex("by_scope_window", (q) => q.eq("scopeType", "license").eq("scopeId", licenseScopeId).eq("window", "daily").eq("windowStart", day))
        .unique()
      : null;
    const monthlyLicenseWindow = licenseScopeId
      ? await ctx.db.query("quotaWindows")
        .withIndex("by_scope_window", (q) => q.eq("scopeType", "license").eq("scopeId", licenseScopeId).eq("window", "monthly").eq("windowStart", month))
        .unique()
      : null;
    const requested = Math.max(0, Number(args.requestedTokens || 0));
    if (config.perMachineDailyLimit && (dailyMachineWindow?.totalTokens || 0) + requested > config.perMachineDailyLimit) {
      return { allowed: false, reason: "daily machine token limit reached", entitlement, config };
    }
    if (entitlement.quota?.dailyTokenLimit && dailyLicenseWindow && dailyLicenseWindow.totalTokens + requested > entitlement.quota.dailyTokenLimit) {
      return { allowed: false, reason: "daily token limit reached", entitlement, config };
    }
    if (entitlement.quota?.monthlyTokenLimit && monthlyLicenseWindow && monthlyLicenseWindow.totalTokens + requested > entitlement.quota.monthlyTokenLimit) {
      return { allowed: false, reason: "monthly token limit reached", entitlement, config };
    }

    return { allowed: true, entitlement, config, providerSecret };
  },
});

export const recordUsageAndQuota = mutation({
  args: {
    machineId: v.string(),
    deviceId: v.optional(v.id("devices")),
    licenseId: v.optional(v.id("licenses")),
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
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    const timestamp = now();
    const machineId = normalizeMachineId(args.machineId);
    await ctx.db.insert("usageEvents", {
      ...withoutUndefined(args),
      machineId,
      createdAt: timestamp,
    });

    const windows = [
      { scopeType: "machine" as const, scopeId: machineId, window: "daily" as const, windowStart: dayStart(timestamp) },
      { scopeType: "machine" as const, scopeId: machineId, window: "monthly" as const, windowStart: monthStart(timestamp) },
    ];
    if (args.licenseId) {
      windows.push(
        { scopeType: "license" as const, scopeId: String(args.licenseId), window: "daily" as const, windowStart: dayStart(timestamp) },
        { scopeType: "license" as const, scopeId: String(args.licenseId), window: "monthly" as const, windowStart: monthStart(timestamp) },
      );
    }

    for (const quotaWindow of windows) {
      const existing = await ctx.db.query("quotaWindows")
        .withIndex("by_scope_window", (q) => q
          .eq("scopeType", quotaWindow.scopeType)
          .eq("scopeId", quotaWindow.scopeId)
          .eq("window", quotaWindow.window)
          .eq("windowStart", quotaWindow.windowStart))
        .unique();
      if (existing) {
        await ctx.db.patch(existing._id, {
          totalTokens: existing.totalTokens + args.totalTokens,
          updatedAt: timestamp,
        });
      } else {
        await ctx.db.insert("quotaWindows", {
          ...quotaWindow,
          totalTokens: args.totalTokens,
          updatedAt: timestamp,
        });
      }
    }
  },
});

export const generateLlm = action({
  args: {
    machineId: v.string(),
    activationHash: v.optional(v.string()),
    source: v.optional(v.string()),
    projectId: v.optional(v.string()),
    threadId: v.optional(v.string()),
    mode: v.optional(v.string()),
    systemPrompt: v.optional(v.string()),
    userMessage: v.optional(v.string()),
    prompt: v.optional(v.string()),
    messages: v.optional(v.any()),
    tools: v.optional(v.any()),
    toolChoice: v.optional(v.any()),
    model: v.optional(v.string()),
    temperature: v.optional(v.number()),
    maxTokens: v.optional(v.number()),
    extraBody: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    const requestedTokens = tokenEstimateFromMessages(args.messages || args.userMessage || args.prompt);
    const access: any = await ctx.runQuery(api.controlPlane.getGatewayAccess, {
      machineId: args.machineId,
      requestedTokens,
    });
    if (!access.allowed) {
      return {
        ok: false,
        error: {
          code: "quota_or_entitlement_denied",
          message: access.reason || "This machine is not allowed to use Chipix cloud LLM.",
        },
      };
    }

    const provider = access.config.provider;
    const envKey = String(access.providerSecret.envKey || "").trim();
    const apiKey = envKey ? process.env[envKey] : "";
    if (!apiKey) {
      return {
        ok: false,
        error: {
          code: "provider_key_missing",
          message: `Provider key environment variable '${envKey || "(empty)"}' is not configured in Convex.`,
        },
      };
    }

    try {
      const result = provider === "gemini"
        ? await callGemini(args, access, apiKey)
        : await callOpenAiCompatible(args, access, apiKey);
      await ctx.runMutation(api.controlPlane.recordUsageAndQuota, withoutUndefined({
        machineId: args.machineId,
        deviceId: access.entitlement.deviceId || undefined,
        licenseId: access.entitlement.licenseId || undefined,
        provider: result.provider,
        model: result.model,
        source: args.source || args.mode || "backend",
        projectId: args.projectId,
        threadId: args.threadId,
        inputTokens: result.usage.inputTokens,
        outputTokens: result.usage.outputTokens,
        totalTokens: result.usage.totalTokens,
        success: true,
        metadata: { mode: args.mode || "generate" },
      }));
      return { ok: true, ...result };
    } catch (error: any) {
      const fallbackTokens = Math.max(1, requestedTokens);
      await ctx.runMutation(api.controlPlane.recordUsageAndQuota, withoutUndefined({
        machineId: args.machineId,
        deviceId: access.entitlement.deviceId || undefined,
        licenseId: access.entitlement.licenseId || undefined,
        provider,
        model: args.model || access.config.model,
        source: args.source || args.mode || "backend",
        projectId: args.projectId,
        threadId: args.threadId,
        inputTokens: fallbackTokens,
        outputTokens: 0,
        totalTokens: fallbackTokens,
        success: false,
        errorClass: error?.body?.error?.code || error?.name || "provider_error",
        metadata: { status: error?.status, message: error?.message },
      }));
      return {
        ok: false,
        error: {
          code: error?.body?.error?.code || "provider_error",
          message: error?.message || "Convex LLM gateway request failed.",
          status: error?.status || 500,
        },
      };
    }
  },
});

export const upsertRemoteConfig = mutation({
  args: {
    name: v.optional(v.string()),
    provider: providerValidator,
    model: v.string(),
    maxTokens: v.optional(v.number()),
    temperature: v.optional(v.number()),
    dailyTokenLimit: v.optional(v.number()),
    monthlyTokenLimit: v.optional(v.number()),
    perMachineDailyLimit: v.optional(v.number()),
    allowUnknownDevices: v.optional(v.boolean()),
    gatewayEnabled: v.optional(v.boolean()),
    featureFlags: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    const name = args.name || "default";
    const existing = await ctx.db.query("remoteConfig").withIndex("by_name", (q) => q.eq("name", name)).unique();
    const payload = {
      name,
      provider: args.provider,
      model: args.model,
      maxTokens: args.maxTokens ?? DEFAULT_CONFIG.maxTokens,
      temperature: args.temperature ?? DEFAULT_CONFIG.temperature,
      dailyTokenLimit: args.dailyTokenLimit,
      monthlyTokenLimit: args.monthlyTokenLimit,
      perMachineDailyLimit: args.perMachineDailyLimit,
      allowUnknownDevices: args.allowUnknownDevices ?? false,
      gatewayEnabled: args.gatewayEnabled ?? true,
      featureFlags: args.featureFlags ?? {},
      updatedAt: now(),
    };
    if (existing) {
      await ctx.db.patch(existing._id, withoutUndefined(payload));
      return existing._id;
    }
    return await ctx.db.insert("remoteConfig", withoutUndefined(payload));
  },
});

export const upsertProviderSecret = mutation({
  args: {
    provider: providerValidator,
    envKey: v.string(),
    baseUrl: v.optional(v.string()),
    deployment: v.optional(v.string()),
    apiVersion: v.optional(v.string()),
    apiStyle: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const existing = await ctx.db.query("providerSecrets").withIndex("by_provider", (q) => q.eq("provider", args.provider)).unique();
    const payload = { ...args, updatedAt: now() };
    if (existing) {
      await ctx.db.patch(existing._id, withoutUndefined(payload));
      return existing._id;
    }
    return await ctx.db.insert("providerSecrets", withoutUndefined(payload));
  },
});

export const grantDevice = mutation({
  args: {
    machineId: v.string(),
    licenseKey: v.optional(v.string()),
    customerName: v.optional(v.string()),
    email: v.optional(v.string()),
    plan: v.optional(v.string()),
    expiresAt: v.optional(v.number()),
    dailyTokenLimit: v.optional(v.number()),
    monthlyTokenLimit: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const timestamp = now();
    let licenseId = undefined as any;
    if (args.licenseKey) {
      const existingLicense = await ctx.db.query("licenses").withIndex("by_license_key", (q) => q.eq("licenseKey", args.licenseKey!)).unique();
      const licensePayload = {
        licenseKey: args.licenseKey,
        customerName: args.customerName,
        email: args.email,
        plan: args.plan || "demo",
        status: "active" as const,
        expiresAt: args.expiresAt,
        dailyTokenLimit: args.dailyTokenLimit,
        monthlyTokenLimit: args.monthlyTokenLimit,
        updatedAt: timestamp,
      };
      if (existingLicense) {
        await ctx.db.patch(existingLicense._id, withoutUndefined(licensePayload));
        licenseId = existingLicense._id;
      } else {
        licenseId = await ctx.db.insert("licenses", {
          ...withoutUndefined(licensePayload),
          createdAt: timestamp,
        });
      }
    }

    const machineId = normalizeMachineId(args.machineId);
    const existingDevice = await ctx.db.query("devices").withIndex("by_machine_id", (q) => q.eq("machineId", machineId)).unique();
    const devicePayload = {
      machineId,
      licenseId,
      status: "active" as const,
      activatedAt: timestamp,
      updatedAt: timestamp,
    };
    if (existingDevice) {
      await ctx.db.patch(existingDevice._id, withoutUndefined(devicePayload));
      return existingDevice._id;
    }
    return await ctx.db.insert("devices", {
      ...withoutUndefined(devicePayload),
      createdAt: timestamp,
    });
  },
});

export const revokeDevice = mutation({
  args: { machineId: v.string() },
  handler: async (ctx, args) => {
    const machineId = normalizeMachineId(args.machineId);
    const existing = await ctx.db.query("devices").withIndex("by_machine_id", (q) => q.eq("machineId", machineId)).unique();
    if (!existing) {
      return null;
    }
    await ctx.db.patch(existing._id, { status: "revoked", updatedAt: now() });
    return existing._id;
  },
});

export const publishDesktopRelease = mutation({
  args: {
    platform: platformValidator,
    version: v.string(),
    minSupportedVersion: v.optional(v.string()),
    releaseNotes: v.optional(v.string()),
    updateFeedStorageId: v.optional(v.id("_storage")),
    installerStorageId: v.optional(v.id("_storage")),
    updateFeedUrl: v.optional(v.string()),
    downloadUrl: v.optional(v.string()),
    forceUpdate: v.optional(v.boolean()),
  },
  handler: async (ctx, args) => {
    const existing = await ctx.db.query("desktopReleases").withIndex("by_platform", (q) => q.eq("platform", args.platform)).unique();
    const payload: Record<string, unknown> = {
      ...args,
      forceUpdate: args.forceUpdate ?? false,
      publishedAt: now(),
      updatedAt: now(),
    };
    // GitHub-hosted feeds should not keep stale Convex Storage blob references.
    if (args.updateFeedUrl) {
      payload.updateFeedStorageId = undefined;
    }
    if (args.updateFeedStorageId) {
      payload.updateFeedUrl = undefined;
    }
    if (args.downloadUrl) {
      payload.installerStorageId = undefined;
    }
    if (args.installerStorageId) {
      payload.downloadUrl = undefined;
    }
    if (existing) {
      const patch: Record<string, unknown> = withoutUndefined(payload);
      if (args.updateFeedUrl) {
        patch.updateFeedStorageId = undefined;
      }
      if (args.updateFeedStorageId) {
        patch.updateFeedUrl = undefined;
      }
      if (args.downloadUrl) {
        patch.installerStorageId = undefined;
      }
      if (args.installerStorageId) {
        patch.downloadUrl = undefined;
      }
      await ctx.db.patch(existing._id, patch);
      return existing._id;
    }
    return await ctx.db.insert("desktopReleases", withoutUndefined(payload));
  },
});

export const createDesktopReleaseUploadUrl = mutation({
  args: {
    kind: v.optional(v.union(
      v.literal("update_feed"),
      v.literal("installer"),
    )),
  },
  handler: async (ctx) => {
    const uploadUrl = await ctx.storage.generateUploadUrl();
    return { uploadUrl };
  },
});

export const seedCloudDefaults = mutation({
  args: {},
  handler: async (ctx) => {
    const existingConfig = await ctx.db.query("remoteConfig").withIndex("by_name", (q) => q.eq("name", "default")).unique();
    if (!existingConfig) {
      await ctx.db.insert("remoteConfig", {
        ...DEFAULT_CONFIG,
        updatedAt: now(),
      });
    }
    const existingBedrockSecret = await ctx.db.query("providerSecrets").withIndex("by_provider", (q) => q.eq("provider", "bedrock")).unique();
    if (!existingBedrockSecret) {
      await ctx.db.insert("providerSecrets", {
        provider: "bedrock",
        envKey: "BEDROCK_API_KEY",
        baseUrl: `https://bedrock-mantle.${process.env.BEDROCK_REGION || "us-east-1"}.api.aws/v1`,
        updatedAt: now(),
      });
    }
    return { ok: true };
  },
});
