/**
 * Visual-only orbital metrics. Counts represent real knowledge points:
 * chapter counts are calculated from contains edges; browse-group counts
 * are the number of members. No semantic relations are inferred here.
 *
 * The seed only adds a small stable fractional-period offset. The largest
 * possible random difference is smaller than the increase for ONE extra
 * knowledge point, so for ANY two IDs: more knowledge is ALWAYS slower.
 */
export type OrbitBodyKind = 'chapter' | 'group'

function stableFraction(seed: string): number {
  let hash = 2166136261
  for (let i = 0; i < seed.length; i++)
    hash = Math.imul(hash ^ seed.charCodeAt(i), 16777619)
  return (hash >>> 0) / 4294967295
}

export function orbitMetrics(kind: OrbitBodyKind, rawCount: number, id: string) {
  const count = Math.max(0, Math.floor(Number.isFinite(rawCount) ? rawCount : 0))
  // Radius is in SCREEN pixels. Never use a fixed world radius that balloons
  // when the 3D camera zooms into the map. Limits handle unusually large sets.
  const idealRadiusPx = kind === 'chapter'
    ? Math.min(60, 11 + .155 * count)
    : Math.min(36, 10 + 1.5 * count)
  const jitter = stableFraction(id + ':orbit-period') - .5
  const periodSeconds = kind === 'chapter'
    ? 19 + .20 * count + .15 * jitter
    : 14 + 2.25 * count + .65 * jitter
  return {count, idealRadiusPx, periodSeconds}
}
