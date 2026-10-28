#!/usr/bin/env node
// scope-graph.mjs: a deterministic TS/JS import graph over candidate project scopes.
//
//   node scope-graph.mjs --root <worktree> --scope api='services/api/**' \
//     --scope web='web/src/**,packages/ui/**' [--out plan-analysis.json]
//   node scope-graph.mjs --selftest
//
// rg lists the source files (respecting .gitignore; a plain directory walk when rg
// is missing or --no-rg is given). A resolver follows relative imports, tsconfig
// `paths`/`baseUrl` (with relative `extends`) and workspace package names. The
// JSON says how cohesive each scope is, which scopes import each other, which
// files several scopes share (hubs) and which files two scope globs both claim.
// No network, no dependencies, the same output for the same tree.

import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, posix, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

export const VERSION = 1;
const SOURCE_EXT = ['.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs'];
const SKIP_DIRS = new Set(['node_modules', '.git', 'dist', 'build', 'coverage', '.next', '.turbo']);
const EXAMPLES = 5;
const HUBS = 25;

// ── Globs ────────────────────────────────────────────────────────

function expandBraces(glob) {
  const match = /\{([^{}]*)\}/.exec(glob);
  if (!match) return [glob];
  const [head, tail] = [glob.slice(0, match.index), glob.slice(match.index + match[0].length)];
  return match[1].split(',').flatMap((part) => expandBraces(head + part + tail));
}

/** A glob (`**`, `*`, `?`, `{a,b}`) as a RegExp over repo-relative posix paths. */
export function globToRegExp(glob) {
  let pattern = glob.trim().replace(/^\.\//, '').replace(/\/+$/, '');
  if (!/[*?{]/.test(pattern)) pattern += '/**';
  const alternatives = expandBraces(pattern).map((one) => {
    let out = '';
    for (let i = 0; i < one.length; i += 1) {
      const ch = one[i];
      if (ch === '*' && one[i + 1] === '*') {
        const slash = one[i + 2] === '/';
        out += slash ? '(?:.*/)?' : '.*';
        i += slash ? 2 : 1;
      } else if (ch === '*') out += '[^/]*';
      else if (ch === '?') out += '[^/]';
      else out += ch.replace(/[.+^${}()|[\]\\]/g, '\\$&');
    }
    return out;
  });
  return new RegExp(`^(?:${alternatives.join('|')})$`);
}

// ── Files ────────────────────────────────────────────────────────

function toPosix(path) {
  return path.split(sep).join('/');
}

function walk(root, dir = root, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))) {
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name)) walk(root, join(dir, entry.name), out);
    } else if (entry.isFile()) {
      out.push(toPosix(relative(root, join(dir, entry.name))));
    }
  }
  return out;
}

/** Every file under *root* (rg when available), repo-relative and sorted. */
export function listFiles(root, { useRg = true } = {}) {
  if (useRg) {
    const rg = spawnSync('rg', ['--files', '--hidden', '-g', '!.git', '-g', '!node_modules'], {
      cwd: root,
      encoding: 'utf8',
      maxBuffer: 256 * 1024 * 1024,
    });
    if (rg.status === 0 || (rg.status === 1 && !rg.stderr)) {
      return rg.stdout.split('\n').filter(Boolean).map(toPosix).sort();
    }
  }
  return walk(root).sort();
}

// ── Imports ──────────────────────────────────────────────────────

const IMPORT_PATTERNS = [
  /\bfrom\s*['"]([^'"\n]+)['"]/g,
  /\bimport\s*['"]([^'"\n]+)['"]/g,
  /\b(?:import|require)\s*\(\s*['"]([^'"\n]+)['"]\s*\)/g,
];

/** The module specifiers a source file imports, in order, without repeats. */
export function importsOf(text) {
  const code = text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const found = new Set();
  for (const pattern of IMPORT_PATTERNS) {
    for (const match of code.matchAll(pattern)) found.add(match[1]);
  }
  return [...found];
}

// ── Resolver ─────────────────────────────────────────────────────

function readJson(path) {
  try {
    const raw = readFileSync(path, 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
      .replace(/,(\s*[}\]])/g, '$1');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

class Resolver {
  constructor(root, files) {
    this.root = root;
    this.files = new Set(files);
    this.tsconfigs = new Map();
    this.packages = new Map();
    for (const file of files) {
      if (posix.basename(file) !== 'package.json') continue;
      const pkg = readJson(join(root, file));
      if (pkg && typeof pkg.name === 'string') {
        this.packages.set(pkg.name, { dir: posix.dirname(file), pkg });
      }
    }
  }

  candidates(base) {
    const stem = base.replace(/\.(?:[cm]?js|jsx)$/, '');
    const out = [base];
    for (const ext of SOURCE_EXT) out.push(stem + ext, `${base}${ext}`);
    for (const ext of SOURCE_EXT) out.push(`${base}/index${ext}`);
    return out;
  }

  file(base) {
    const clean = posix.normalize(base).replace(/^\.\//, '');
    if (clean.startsWith('..')) return null;
    return this.candidates(clean).find((c) => this.files.has(c)) ?? null;
  }

  tsconfig(dir) {
    if (this.tsconfigs.has(dir)) return this.tsconfigs.get(dir);
    let found = null;
    if (this.files.has(posix.join(dir, 'tsconfig.json'))) {
      found = this.loadTsconfig(posix.join(dir, 'tsconfig.json'), 0);
    } else if (dir !== '.' && dir !== '') {
      found = this.tsconfig(posix.dirname(dir) || '.');
    }
    this.tsconfigs.set(dir, found);
    return found;
  }

  loadTsconfig(path, depth) {
    const json = readJson(join(this.root, path));
    if (!json) return null;
    const dir = posix.dirname(path);
    let merged = { baseUrl: null, paths: {}, dir };
    if (typeof json.extends === 'string' && json.extends.startsWith('.') && depth < 5) {
      const parent = this.loadTsconfig(posix.normalize(posix.join(dir, json.extends.endsWith('.json') ? json.extends : `${json.extends}.json`)), depth + 1);
      if (parent) merged = { ...parent };
    }
    const options = json.compilerOptions || {};
    if (typeof options.baseUrl === 'string') merged.baseUrl = posix.normalize(posix.join(dir, options.baseUrl));
    if (options.paths && typeof options.paths === 'object') {
      merged.paths = options.paths;
      merged.dir = dir;
    }
    return merged;
  }

  aliased(spec, fromDir) {
    const config = this.tsconfig(fromDir);
    if (!config) return null;
    const base = config.baseUrl ?? config.dir;
    for (const [pattern, targets] of Object.entries(config.paths || {}).sort()) {
      const star = pattern.indexOf('*');
      const [prefix, suffix] = star < 0 ? [pattern, ''] : [pattern.slice(0, star), pattern.slice(star + 1)];
      const hit = star < 0 ? spec === pattern : spec.startsWith(prefix) && spec.endsWith(suffix);
      if (!hit) continue;
      const middle = star < 0 ? '' : spec.slice(prefix.length, spec.length - suffix.length);
      for (const target of targets) {
        const found = this.file(posix.join(base, target.replace('*', middle)));
        if (found) return found;
      }
    }
    if (config.baseUrl) return this.file(posix.join(config.baseUrl, spec));
    return null;
  }

  workspace(spec) {
    const names = [...this.packages.keys()].filter((n) => spec === n || spec.startsWith(`${n}/`));
    const name = names.sort((a, b) => b.length - a.length)[0];
    if (!name) return null;
    const { dir, pkg } = this.packages.get(name);
    const sub = spec.slice(name.length + 1);
    if (sub) return this.file(posix.join(dir, sub)) ?? this.file(posix.join(dir, 'src', sub));
    const entries = [pkg.source, pkg.types, typeof pkg.exports === 'string' ? pkg.exports : null, pkg.main]
      .filter((e) => typeof e === 'string')
      .map((e) => e.replace(/^\.\//, '').replace(/^dist\//, 'src/').replace(/\.d\.ts$/, '.ts'));
    for (const entry of [...entries, 'src/index', 'index']) {
      const found = this.file(posix.join(dir, entry));
      if (found) return found;
    }
    return null;
  }

  /** The repo file *spec* (imported from *from*) names, or null for externals. */
  resolve(spec, from) {
    const fromDir = posix.dirname(from);
    if (spec.startsWith('.')) return this.file(posix.join(fromDir, spec));
    if (spec.startsWith('/') || spec.startsWith('node:')) return null;
    return this.aliased(spec, fromDir) ?? this.workspace(spec);
  }
}

// ── Analysis ─────────────────────────────────────────────────────

function parseScopes(raw) {
  const scopes = {};
  for (const [name, globs] of Object.entries(raw)) {
    const list = (Array.isArray(globs) ? globs : String(globs).split(',')).map((g) => g.trim()).filter(Boolean);
    scopes[name] = { globs: list, patterns: list.map(globToRegExp) };
  }
  return scopes;
}

/**
 * Analyse *root* for the candidate *scopes* (`{name: "glob,glob" | [globs]}`).
 * @returns {object} The JSON report (deterministic for a given tree).
 */
export function analyse(root, rawScopes, { useRg = true } = {}) {
  const scopes = parseScopes(rawScopes);
  const names = Object.keys(scopes).sort();
  const all = listFiles(root, { useRg });
  const sources = all.filter((f) => SOURCE_EXT.some((ext) => f.endsWith(ext)) && !f.endsWith('.d.ts'));
  const resolver = new Resolver(root, all);
  const scopesOf = (file) => names.filter((n) => scopes[n].patterns.some((p) => p.test(file)));
  const fileScopes = new Map(sources.map((f) => [f, scopesOf(f)]));
  const edges = [];
  let unresolved = 0;
  for (const from of sources) {
    const text = readFileSync(join(root, from), 'utf8');
    for (const spec of importsOf(text)) {
      const to = resolver.resolve(spec, from);
      if (to === null) {
        if (spec.startsWith('.')) unresolved += 1;
        continue;
      }
      if (to !== from) edges.push([from, to]);
    }
  }
  edges.sort((a, b) => (a.join('\0') < b.join('\0') ? -1 : a.join('\0') > b.join('\0') ? 1 : 0));

  const stats = Object.fromEntries(names.map((n) => [n, { globs: scopes[n].globs, files: 0, internal: 0, out: 0, in: 0 }]));
  for (const [file, owners] of fileScopes) for (const n of owners) stats[n].files += 1;
  const cross = new Map();
  const importers = new Map();
  for (const [from, to] of edges) {
    const fromScopes = fileScopes.get(from) ?? [];
    const toScopes = fileScopes.get(to) ?? scopesOf(to);
    for (const a of fromScopes) {
      if (toScopes.includes(a)) {
        stats[a].internal += 1;
        continue;
      }
      stats[a].out += 1;
      for (const b of toScopes) {
        stats[b].in += 1;
        const key = `${a}\0${b}`;
        const entry = cross.get(key) ?? { from: a, to: b, edges: 0, examples: [] };
        entry.edges += 1;
        if (entry.examples.length < EXAMPLES) entry.examples.push([from, to]);
        cross.set(key, entry);
      }
      const seen = importers.get(to) ?? new Set();
      seen.add(a);
      importers.set(to, seen);
    }
  }
  for (const n of names) {
    const s = stats[n];
    s.cohesion = s.internal + s.out === 0 ? null : Number((s.internal / (s.internal + s.out)).toFixed(3));
  }
  const inDegree = new Map();
  for (const [, to] of edges) inDegree.set(to, (inDegree.get(to) ?? 0) + 1);
  const hubs = [...importers.entries()]
    .filter(([, from]) => from.size >= 2)
    .map(([file, from]) => ({ file, in: inDegree.get(file) ?? 0, from: [...from].sort(), scopes: fileScopes.get(file) ?? scopesOf(file) }))
    .sort((a, b) => b.from.length - a.from.length || b.in - a.in || (a.file < b.file ? -1 : 1))
    .slice(0, HUBS);
  const overlaps = [];
  for (let i = 0; i < names.length; i += 1) {
    for (let j = i + 1; j < names.length; j += 1) {
      const shared = all.filter((f) => scopesOf(f).includes(names[i]) && scopesOf(f).includes(names[j]));
      if (shared.length) overlaps.push({ scopes: [names[i], names[j]], count: shared.length, files: shared.slice(0, 10) });
    }
  }
  const unscoped = hubs.filter((h) => h.scopes.length === 0).map((h) => h.file);
  return {
    tool: 'scope-graph.mjs',
    version: VERSION,
    files: sources.length,
    edges: edges.length,
    unresolved_relative: unresolved,
    scopes: stats,
    cross_scope: [...cross.values()].sort((a, b) => b.edges - a.edges || (a.from + a.to < b.from + b.to ? -1 : 1)),
    hubs,
    shared_outside_scopes: unscoped,
    overlaps,
  };
}

// ── CLI ──────────────────────────────────────────────────────────

function parseArgs(argv) {
  const out = { root: '.', scopes: {}, out: null, useRg: true, selftest: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--selftest') out.selftest = true;
    else if (arg === '--no-rg') out.useRg = false;
    else if (arg === '--root') out.root = argv[++i];
    else if (arg === '--out') out.out = argv[++i];
    else if (arg === '--scope') {
      const value = argv[++i] ?? '';
      const eq = value.indexOf('=');
      if (eq <= 0) throw new Error(`--scope needs name=glob[,glob], got ${JSON.stringify(value)}`);
      out.scopes[value.slice(0, eq)] = value.slice(eq + 1);
    } else throw new Error(`unknown argument ${arg}`);
  }
  return out;
}

export const FIXTURE_SCOPES = {
  api: 'services/api/**',
  db: 'services/db/**',
  web: 'web/src/**',
  contracts: 'services/*/src/contract.ts',
};

/** The report the shipped fixture must produce (see fixture/). */
export const FIXTURE_EXPECTED = {
  files: 11,
  edges: 10,
  cross_scope: [['web', 'api', 1]],
  hubs: [
    ['packages/shared/src/version.ts', ['api', 'db']],
    ['packages/shared/src/index.ts', ['api', 'web']],
  ],
  overlaps: [
    [['api', 'contracts'], 1],
    [['contracts', 'db'], 1],
  ],
  cohesion: { api: 0.333, contracts: null, db: 0.5, web: 0.5 },
};

export function checkFixture(report) {
  const problems = [];
  const expect = (what, got, want) => {
    if (JSON.stringify(got) !== JSON.stringify(want)) problems.push(`${what}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  };
  expect('files', report.files, FIXTURE_EXPECTED.files);
  expect('edges', report.edges, FIXTURE_EXPECTED.edges);
  expect('cross_scope', report.cross_scope.map((c) => [c.from, c.to, c.edges]), FIXTURE_EXPECTED.cross_scope);
  expect('hubs', report.hubs.map((h) => [h.file, h.from]), FIXTURE_EXPECTED.hubs);
  expect('overlaps', report.overlaps.map((o) => [o.scopes, o.count]), FIXTURE_EXPECTED.overlaps);
  expect('shared_outside_scopes', report.shared_outside_scopes, FIXTURE_EXPECTED.hubs.map((h) => h[0]));
  expect('unresolved_relative', report.unresolved_relative, 0);
  const cohesion = Object.fromEntries(Object.entries(report.scopes).map(([n, v]) => [n, v.cohesion]));
  expect('cohesion', cohesion, FIXTURE_EXPECTED.cohesion);
  return problems;
}

function selftest() {
  const fixture = join(dirname(fileURLToPath(import.meta.url)), 'fixture');
  const problems = [];
  for (const useRg of [true, false]) {
    const report = analyse(fixture, FIXTURE_SCOPES, { useRg });
    problems.push(...checkFixture(report).map((p) => `${useRg ? 'rg' : 'walk'}: ${p}`));
  }
  if (problems.length) {
    console.error(problems.join('\n'));
    return 1;
  }
  console.log('SELFTEST OK');
  return 0;
}

function main(argv) {
  const args = parseArgs(argv);
  if (args.selftest) return selftest();
  if (!Object.keys(args.scopes).length) throw new Error('give at least one --scope name=glob');
  const root = resolve(args.root);
  if (!existsSync(root) || !statSync(root).isDirectory()) throw new Error(`no directory ${root}`);
  const report = analyse(root, args.scopes, { useRg: args.useRg });
  const json = `${JSON.stringify(report, null, 2)}\n`;
  if (args.out) writeFileSync(args.out, json);
  else process.stdout.write(json);
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = main(process.argv.slice(2));
  } catch (error) {
    console.error(`scope-graph: ${error.message}`);
    process.exitCode = 2;
  }
}
