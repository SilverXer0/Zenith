import { existsSync } from "node:fs";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { promisify } from "node:util";
import { execFile } from "node:child_process";
import { randomBytes, scryptSync } from "node:crypto";

const execFileAsync = promisify(execFile);
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const dataDir = process.env.ZENITH_DATA_DIR || join(root, "data");
const databaseFile = join(dataDir, "zenith.sqlite");
const sqlite = process.env.ZENITH_SQLITE || "sqlite3";

const sqlQuote = (value) => `'${String(value).replaceAll("'", "''")}'`;
const passwordHash = (password) => {
  const salt = randomBytes(16).toString("hex");
  return `${salt}:${scryptSync(password, salt, 64).toString("hex")}`;
};

function question(rl, prompt) {
  return new Promise((resolve) => rl.question(prompt, resolve));
}

async function runSql(sql, json = false) {
  const args = json ? ["-json", databaseFile, sql] : [databaseFile, sql];
  const { stdout } = await execFileAsync(sqlite, args, { maxBuffer: 2 * 1024 * 1024 });
  return json ? (stdout.trim() ? JSON.parse(stdout) : []) : stdout;
}

async function main() {
  if (!existsSync(databaseFile)) {
    throw new Error(`Zenith database not found at ${databaseFile}. Run Zenith once first.`);
  }

  const users = await runSql("SELECT id, display_name AS displayName FROM users ORDER BY created_at", true);
  if (!users.length) throw new Error("No Zenith account was found in the database.");

  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try {
    console.log("Zenith local passphrase recovery");
    console.log(`Database: ${databaseFile}`);
    console.log("Existing account(s):");
    users.forEach((user, index) => console.log(`  ${index + 1}. ${user.displayName}`));
    const selected = users.length === 1 ? users[0] : users[Number(await question(rl, "Choose account number: ")) - 1];
    if (!selected) throw new Error("That account number is not valid.");
    const confirmation = await question(rl, `Type RESET to change the passphrase for ${selected.displayName}: `);
    if (confirmation.trim() !== "RESET") {
      console.log("Recovery cancelled. No changes were made.");
      return;
    }
    const password = await question(rl, "New passphrase (8+ characters): ");
    if (password.length < 8) throw new Error("Use a passphrase with at least 8 characters.");
    const confirmationPassword = await question(rl, "Repeat new passphrase: ");
    if (password !== confirmationPassword) throw new Error("The passphrases do not match.");
    await runSql(`UPDATE users SET password_hash=${sqlQuote(passwordHash(password))} WHERE id=${sqlQuote(selected.id)}; DELETE FROM sessions WHERE user_id=${sqlQuote(selected.id)}`);
    console.log(`Passphrase reset. Sign in using display name: ${selected.displayName}`);
  } finally {
    rl.close();
  }
}

main().catch((error) => {
  console.error(`Recovery failed: ${error.message}`);
  process.exitCode = 1;
});
