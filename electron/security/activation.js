const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

// Demo/offline activation secret. For a real release, replace this per build
// and keep the issuing service isolated from the shipped desktop binary.
const DEFAULT_LICENSE_SECRET = "chipverify-desktop-demo-license-v1-change-before-release";

function getLicenseSecret() {
  return String(process.env.CHIPVERIFY_LICENSE_SECRET || DEFAULT_LICENSE_SECRET).trim();
}

function normalizeCode(value) {
  return String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
}

function formatGroups(prefix, value, groupCount = 5) {
  const compact = normalizeCode(value).slice(0, groupCount * 4);
  const groups = compact.match(/.{1,4}/g) || [];
  return `${prefix}-${groups.join("-")}`;
}

function readWindowsMachineGuid() {
  if (process.platform !== "win32") {
    return "";
  }

  try {
    const output = execFileSync(
      "reg",
      ["query", "HKLM\\SOFTWARE\\Microsoft\\Cryptography", "/v", "MachineGuid"],
      { encoding: "utf8", timeout: 1500, windowsHide: true },
    );
    const match = output.match(/MachineGuid\s+REG_SZ\s+([^\r\n]+)/i);
    return match ? match[1].trim() : "";
  } catch {
    return "";
  }
}

function getMachineSeed() {
  const user = (() => {
    try {
      return os.userInfo().username || "";
    } catch {
      return "";
    }
  })();

  const machineGuid = readWindowsMachineGuid();
  return [
    "chipverify-desktop",
    os.platform(),
    os.arch(),
    os.hostname(),
    machineGuid || "no-machine-guid",
    machineGuid ? "" : user,
  ].join("|");
}

function getMachineId() {
  const digest = crypto.createHash("sha256").update(getMachineSeed()).digest("hex").toUpperCase();
  return formatGroups("CVM", digest, 5);
}

function normalizeMachineId(machineId) {
  return normalizeCode(machineId);
}

function normalizeMachineKey(machineKey) {
  return normalizeCode(machineKey);
}

function expectedMachineKey(machineId, secret = getLicenseSecret()) {
  const normalizedMachineId = normalizeMachineId(machineId);
  const digest = crypto
    .createHmac("sha256", String(secret || ""))
    .update(normalizedMachineId)
    .digest("hex")
    .toUpperCase();
  return formatGroups("CVK", digest, 6);
}

function hashActivation(machineId, machineKey) {
  return crypto
    .createHash("sha256")
    .update(`${normalizeMachineId(machineId)}:${normalizeMachineKey(machineKey)}`)
    .digest("hex");
}

function timingSafeStringEqual(a, b) {
  const left = Buffer.from(String(a || ""));
  const right = Buffer.from(String(b || ""));
  if (left.length !== right.length) {
    return false;
  }
  return crypto.timingSafeEqual(left, right);
}

function verifyMachineKey(machineId, machineKey, secret = getLicenseSecret()) {
  const expected = normalizeMachineKey(expectedMachineKey(machineId, secret));
  const actual = normalizeMachineKey(machineKey);
  return timingSafeStringEqual(actual, expected);
}

function readActivationFile(activationPath) {
  try {
    if (!activationPath || !fs.existsSync(activationPath)) {
      return null;
    }
    return JSON.parse(fs.readFileSync(activationPath, "utf8"));
  } catch {
    return null;
  }
}

function isStoredActivationValid(activationPath, machineId = getMachineId()) {
  const state = readActivationFile(activationPath);
  if (!state || state.version !== 1) {
    return false;
  }

  if (normalizeMachineId(state.machineId) !== normalizeMachineId(machineId)) {
    return false;
  }

  const expectedKey = expectedMachineKey(machineId);
  const expectedHash = hashActivation(machineId, expectedKey);
  return timingSafeStringEqual(String(state.activationHash || ""), expectedHash);
}

function getActivationStatus(activationPath, options = {}) {
  const requiresActivation = Boolean(options.requiresActivation);
  const machineId = getMachineId();
  const state = readActivationFile(activationPath);
  const storedActivationValid = isStoredActivationValid(activationPath, machineId);

  if (!requiresActivation) {
    return {
      requiresActivation: false,
      activated: true,
      bypassed: true,
      machineId,
      activatedAt: state?.activatedAt || null,
      activationHash: state?.activationHash || null,
    };
  }

  return {
    requiresActivation: true,
    activated: storedActivationValid,
    bypassed: false,
    machineId,
    activatedAt: storedActivationValid ? state?.activatedAt || null : null,
    activationHash: storedActivationValid ? state?.activationHash || null : null,
  };
}

function activateMachine(activationPath, machineKey, options = {}) {
  const machineId = getMachineId();
  const requiresActivation = options.requiresActivation !== false;

  if (!requiresActivation) {
    return getActivationStatus(activationPath, { requiresActivation: false });
  }

  if (!verifyMachineKey(machineId, machineKey)) {
    return {
      requiresActivation: true,
      activated: false,
      machineId,
      error: "INVALID_MACHINE_KEY",
      message: "Machine key is not valid for this Machine ID.",
    };
  }

  const activationState = {
    version: 1,
    machineId,
    activationHash: hashActivation(machineId, expectedMachineKey(machineId)),
    activatedAt: new Date().toISOString(),
  };

  fs.mkdirSync(path.dirname(activationPath), { recursive: true });
  fs.writeFileSync(activationPath, `${JSON.stringify(activationState, null, 2)}\n`, "utf8");

  return getActivationStatus(activationPath, { requiresActivation: true });
}

module.exports = {
  DEFAULT_LICENSE_SECRET,
  activateMachine,
  expectedMachineKey,
  getActivationStatus,
  getLicenseSecret,
  getMachineId,
  hashActivation,
  normalizeMachineId,
  normalizeMachineKey,
  verifyMachineKey,
};
