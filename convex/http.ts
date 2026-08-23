import { httpRouter } from "convex/server";
import { httpAction } from "./_generated/server";
import { api } from "./_generated/api";

const http = httpRouter();

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json",
    },
  });
}

http.route({
  path: "/api/desktop/version",
  method: "OPTIONS",
  handler: httpAction(async () => new Response(null, { status: 204, headers: corsHeaders })),
});

http.route({
  path: "/api/desktop/version",
  method: "GET",
  handler: httpAction(async (ctx, request) => {
    const url = new URL(request.url);
    const platform = (url.searchParams.get("platform") || "all").toLowerCase();

    const allowed = new Set(["all", "win32", "linux", "darwin"]);
    const normalizedPlatform = allowed.has(platform)
      ? (platform as "all" | "win32" | "linux" | "darwin")
      : "all";

    const row = await ctx.runQuery(api.controlPlane.getDesktopVersion, {
      platform: normalizedPlatform,
    });

    if (!row) {
      return jsonResponse({ error: "No release metadata configured." }, 404);
    }

    const origin = url.origin;
    const feedUrl = row.updateFeedUrl
      ?? (row.updateFeedStorageId ? desktopFeedBaseUrl(origin, row.platform) : "");
    const installerUrl = row.downloadUrl
      ?? (row.installerStorageId ? `${origin}/api/desktop/download?platform=${encodeURIComponent(row.platform)}` : "");

    return jsonResponse({
      platform: row.platform,
      version: row.version,
      minSupportedVersion: row.minSupportedVersion ?? row.version,
      releaseNotes: row.releaseNotes ?? "",
      updateFeedUrl: feedUrl,
      downloadUrl: installerUrl,
      forceUpdate: Boolean(row.forceUpdate),
      updatedAt: row.updatedAt,
    });
  }),
});

http.route({
  path: "/api/desktop/entitlement",
  method: "OPTIONS",
  handler: httpAction(async () => new Response(null, { status: 204, headers: corsHeaders })),
});

http.route({
  path: "/api/desktop/entitlement",
  method: "GET",
  handler: httpAction(async (ctx, request) => {
    const url = new URL(request.url);
    const machineId = url.searchParams.get("machineId") || "";
    if (!machineId.trim()) {
      return jsonResponse({ allowed: false, status: "missing_machine_id" }, 400);
    }
    const entitlement = await ctx.runQuery(api.controlPlane.getEntitlement, {
      machineId,
      platform: url.searchParams.get("platform") || undefined,
      appVersion: url.searchParams.get("appVersion") || undefined,
      activationHash: url.searchParams.get("activationHash") || undefined,
    });
    return jsonResponse(entitlement);
  }),
});

http.route({
  path: "/api/desktop/entitlement",
  method: "POST",
  handler: httpAction(async (ctx, request) => {
    const body = await request.json().catch(() => ({}));
    if (!String(body.machineId || "").trim()) {
      return jsonResponse({ allowed: false, status: "missing_machine_id" }, 400);
    }
    const entitlement = await ctx.runQuery(api.controlPlane.getEntitlement, {
      machineId: String(body.machineId || ""),
      platform: body.platform ? String(body.platform) : undefined,
      appVersion: body.appVersion ? String(body.appVersion) : undefined,
      activationHash: body.activationHash ? String(body.activationHash) : undefined,
    });
    return jsonResponse(entitlement);
  }),
});

http.route({
  path: "/api/desktop/heartbeat",
  method: "OPTIONS",
  handler: httpAction(async () => new Response(null, { status: 204, headers: corsHeaders })),
});

http.route({
  path: "/api/desktop/heartbeat",
  method: "POST",
  handler: httpAction(async (ctx, request) => {
    const body = await request.json().catch(() => ({}));
    if (!String(body.machineId || "").trim()) {
      return jsonResponse({ ok: false, error: "missing_machine_id" }, 400);
    }
    const id = await ctx.runMutation(api.controlPlane.recordHeartbeat, {
      machineId: String(body.machineId || ""),
      platform: body.platform ? String(body.platform) : undefined,
      appVersion: body.appVersion ? String(body.appVersion) : undefined,
      activationHash: body.activationHash ? String(body.activationHash) : undefined,
    });
    return jsonResponse({ ok: true, id });
  }),
});

async function latestFeedResponse(ctx: any, platform: "all" | "win32" | "linux" | "darwin") {
  const row = await ctx.runQuery(api.controlPlane.getDesktopVersion, { platform });
  if (!row?.updateFeedStorageId) {
    return jsonResponse({ error: "No Convex update feed storage id configured." }, 404);
  }
  const blob = await ctx.storage.get(row.updateFeedStorageId);
  if (!blob) {
    return jsonResponse({ error: "Update feed file not found in Convex storage." }, 404);
  }
  return new Response(blob, {
    status: 200,
    headers: {
      ...corsHeaders,
      "Content-Type": "text/yaml; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

for (const platform of ["all", "win32", "linux", "darwin"] as const) {
  http.route({
    path: `/api/desktop/update-feed/${platform}/latest.yml`,
    method: "GET",
    handler: httpAction(async (ctx) => latestFeedResponse(ctx, platform)),
  });
  http.route({
    path: `/api/desktop/update-feed/${platform}/latest-linux.yml`,
    method: "GET",
    handler: httpAction(async (ctx) => latestFeedResponse(ctx, platform)),
  });
  http.route({
    path: `/api/desktop/update-feed/${platform}/latest-mac.yml`,
    method: "GET",
    handler: httpAction(async (ctx) => latestFeedResponse(ctx, platform)),
  });
}

function desktopFeedBaseUrl(origin: string, platform: string) {
  return `${origin}/api/desktop/update-feed/${platform}`;
}

http.route({
  path: "/api/desktop/download",
  method: "GET",
  handler: httpAction(async (ctx, request) => {
    const url = new URL(request.url);
    const platform = (url.searchParams.get("platform") || "win32").toLowerCase();
    const allowed = new Set(["all", "win32", "linux", "darwin"]);
    const normalizedPlatform = allowed.has(platform)
      ? (platform as "all" | "win32" | "linux" | "darwin")
      : "win32";
    const row = await ctx.runQuery(api.controlPlane.getDesktopVersion, {
      platform: normalizedPlatform,
    });
    if (!row?.installerStorageId) {
      return jsonResponse({ error: "No Convex installer storage id configured." }, 404);
    }
    const blob = await ctx.storage.get(row.installerStorageId);
    if (!blob) {
      return jsonResponse({ error: "Installer file not found in Convex storage." }, 404);
    }
    return new Response(blob, {
      status: 200,
      headers: {
        ...corsHeaders,
        "Content-Type": "application/octet-stream",
        "Content-Disposition": `attachment; filename="ChipVerify-Desktop-${row.version}-${normalizedPlatform}"`,
      },
    });
  }),
});

http.route({
  path: "/api/llm/generate",
  method: "OPTIONS",
  handler: httpAction(async () => new Response(null, { status: 204, headers: corsHeaders })),
});

http.route({
  path: "/api/llm/generate",
  method: "POST",
  handler: httpAction(async (ctx, request) => {
    const body = await request.json().catch(() => ({}));
    if (!String(body.machineId || "").trim()) {
      return jsonResponse({
        ok: false,
        error: {
          code: "missing_machine_id",
          message: "Convex LLM gateway requires a desktop machine ID.",
        },
      }, 400);
    }
    const result = await ctx.runAction(api.controlPlane.generateLlm, {
      machineId: String(body.machineId || ""),
      activationHash: body.activationHash ? String(body.activationHash) : undefined,
      source: body.source ? String(body.source) : undefined,
      projectId: body.projectId ? String(body.projectId) : undefined,
      threadId: body.threadId ? String(body.threadId) : undefined,
      mode: body.mode ? String(body.mode) : undefined,
      systemPrompt: body.systemPrompt ? String(body.systemPrompt) : undefined,
      userMessage: body.userMessage ? String(body.userMessage) : undefined,
      prompt: body.prompt ? String(body.prompt) : undefined,
      messages: body.messages,
      tools: body.tools,
      toolChoice: body.toolChoice,
      model: body.model ? String(body.model) : undefined,
      temperature: typeof body.temperature === "number" ? body.temperature : undefined,
      maxTokens: typeof body.maxTokens === "number" ? body.maxTokens : undefined,
      extraBody: body.extraBody,
    });
    return jsonResponse(result, result?.ok === false ? 400 : 200);
  }),
});

export default http;
