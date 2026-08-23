#!/usr/bin/env node

const {
  expectedMachineKey,
  getMachineId,
  normalizeMachineId,
} = require("../electron/security/activation");

const requestedMachineId = process.argv.slice(2).join("").trim();
const machineId = requestedMachineId || getMachineId();

if (!normalizeMachineId(machineId)) {
  console.error("Usage: node scripts/make-machine-key.js <Machine ID>");
  process.exit(1);
}

console.log(`Machine ID: ${machineId}`);
console.log(`Machine Key: ${expectedMachineKey(machineId)}`);
