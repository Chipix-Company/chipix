/**
 * pdf.js 5.x expects a few newer JavaScript helpers that are not available in
 * every Electron/Chromium runtime we ship against. Keep these small polyfills
 * local to the PDF viewer/worker path so the rest of the app stays untouched.
 */
function uint8ArrayToHex(bytes) {
  let out = "";
  for (let i = 0; i < bytes.length; i += 1) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

if (typeof Uint8Array !== "undefined" && typeof Uint8Array.prototype.toHex !== "function") {
  // eslint-disable-next-line no-extend-native
  Uint8Array.prototype.toHex = function toHex() {
    return uint8ArrayToHex(this);
  };
}

function installGetOrInsertComputed(proto) {
  if (!proto || typeof proto.getOrInsertComputed === "function") return;
  Object.defineProperty(proto, "getOrInsertComputed", {
    configurable: true,
    writable: true,
    value(key, callbackfn) {
      if (this.has(key)) return this.get(key);
      const value = callbackfn(key);
      this.set(key, value);
      return value;
    },
  });
}

function installGetOrInsert(proto) {
  if (!proto || typeof proto.getOrInsert === "function") return;
  Object.defineProperty(proto, "getOrInsert", {
    configurable: true,
    writable: true,
    value(key, value) {
      if (this.has(key)) return this.get(key);
      this.set(key, value);
      return value;
    },
  });
}

if (typeof Map !== "undefined") {
  installGetOrInsertComputed(Map.prototype);
  installGetOrInsert(Map.prototype);
}

if (typeof WeakMap !== "undefined") {
  installGetOrInsertComputed(WeakMap.prototype);
  installGetOrInsert(WeakMap.prototype);
}
