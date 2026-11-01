import { orbitMetrics } from './orbitMetrics'

export type CourseOrbitInput = {
  id: string
  knowledgeCount: number
}
export type CenteredOrbit = {
  id: string
  count: number
  major: number
  minor: number
  phase: number
  radiusPx: number
  periodSeconds: number
}
export type CenteredOrbitPlan = {
  orbits: CenteredOrbit[]
  // Shared viewport-wide scaling. Never change this due to a conjunction.
  pixelScale: number
}

/**
 * One visible, nested ellipse per chapter, all centered on the course star.
 * Distinct lanes cannot overtake/collide, even when their angular speeds
 * differ: the gap between neighboring minor axes exceeds their radii sum.
 *
 * World coordinates are "design pixels". The camera fits the plan to the
 * safe viewport, while planet marker radii apply the SAME uniform scale.
 * No per-frame nearest-neighbor size changes are needed.
 */
export function planCenteredCourseOrbits(
  inputs: readonly CourseOrbitInput[],
  aspect: number,
  width: number,
  height: number,
): CenteredOrbitPlan {
  const ratio = Math.max(1.15, Math.min(2.25, Number.isFinite(aspect) ? aspect : 1.6))
  const ordered = inputs.map(({ id, knowledgeCount }) => ({
    id,
    ...orbitMetrics('chapter', knowledgeCount, id),
  })).sort((a, b) => a.count - b.count || a.id.localeCompare(b.id))

  const golden = Math.PI * (3 - Math.sqrt(5))
  const gap = 24
  let prevMinor = 0
  let prevRadius = 0
  const orbits = ordered.map((entry, index): CenteredOrbit => {
    const minor = index === 0
      ? 45 + entry.idealRadiusPx
      : prevMinor + prevRadius + entry.idealRadiusPx + gap
    prevMinor = minor
    prevRadius = entry.idealRadiusPx
    return {
      id: entry.id,
      count: entry.count,
      major: minor * ratio,
      minor,
      // Well-spaced starting phases without any random per-frame movement.
      phase: (index * golden + Math.PI / 2) % (Math.PI * 2),
      radiusPx: entry.idealRadiusPx,
      periodSeconds: entry.periodSeconds,
    }
  })
  const largest = orbits.at(-1)
  if (!largest) return { orbits, pixelScale: 1 }
  const safeWidth = Math.max(200, Number.isFinite(width) ? width : 800)
  const safeHeight = Math.max(180, Number.isFinite(height) ? height : 600)
  const pixelScale = Math.min(
    1,
    safeWidth * .89 / (2 * (largest.major + largest.radiusPx + 24)),
    safeHeight * .86 / (2 * (largest.minor + largest.radiusPx + 24)),
  )
  return { orbits, pixelScale }
}
