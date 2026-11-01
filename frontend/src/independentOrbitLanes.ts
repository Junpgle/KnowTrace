/**
 * Screen-space planning for PRIVATE elliptical lanes. Every planet has its own
 * orbit center and its own ellipse, rather than sharing a circumference and
 * eventually catching up with a slower, larger neighbor.
 *
 * The plane coordinates and radii must use the same units (screen pixels or
 * a consistent world-space projection). The bounds below are conservative:
 * a planet moves at most twice its major radius away from its start; limiting
 * each lane to a quarter of the spare pairwise distance means two complete
 * orbits remain separated at ALL phases, even with different speeds.
 */
export type OrbitAnchor = {
  id: string
  x: number
  y: number
  radius: number
}
export type IndependentOrbitLane = {
  id: string
  centerX: number
  centerY: number
  major: number
  minor: number
  phase: 0
}

export function planIndependentOrbitLanes(
  anchors: readonly OrbitAnchor[],
  maximumMajor: number,
  minimumGap = 12,
): IndependentOrbitLane[] {
  const cap = Number.isFinite(maximumMajor) ? Math.max(0, maximumMajor) : 0
  const gap = Number.isFinite(minimumGap) ? Math.max(0, minimumGap) : 0
  return anchors.map((anchor, i) => {
    let major = cap
    for (let j = 0; j < anchors.length; j++) {
      if (i === j) continue
      const other = anchors[j]
      const distance = Math.hypot(anchor.x - other.x, anchor.y - other.y)
      const clearance = distance - Math.max(0, anchor.radius)
        - Math.max(0, other.radius) - gap
      major = Math.min(major, Math.max(0, clearance / 4))
    }
    return {
      id: anchor.id,
      centerX: anchor.x - major,
      centerY: anchor.y,
      major,
      minor: major * .60,
      phase: 0,
    }
  })
}
