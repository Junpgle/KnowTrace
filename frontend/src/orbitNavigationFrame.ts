import { Vector3 } from 'three'

/**
 * The world-space orbit frame must survive navigation, not just the latest
 * planet coordinates. Re-inferring its axes from the camera AFTER drilling
 * down creates a different ellipse (and scatters planets on return).
 */
export type OrbitFrameBody = {
  id: string
  world: Vector3
  center: Vector3
  axisX: Vector3
  axisY: Vector3
  major: number
  minor: number
  phase: number
  radiansPerSecond: number
  elapsedSeconds: number
}

export type SavedOrbit = {
  id: string
  world: Vector3
  center: Vector3
  axisX: Vector3
  axisY: Vector3
  major: number
  minor: number
  phase: number
  radiansPerSecond: number
}

export function orbitPosition(
  orbit: Pick<SavedOrbit, 'center'|'axisX'|'axisY'|'major'|'minor'|'phase'>,
  phase = orbit.phase,
): Vector3 {
  return orbit.center.clone()
    .addScaledVector(orbit.axisX, orbit.major * Math.cos(phase))
    .addScaledVector(orbit.axisY, orbit.minor * Math.sin(phase))
}

/** Frozen copy, not references into an active WebGL scene. */
export function snapshotOrbitFrame(bodies: readonly OrbitFrameBody[]): Map<string, SavedOrbit> {
  return new Map(bodies.map(body => [body.id, {
    id: body.id,
    world: body.world.clone(),
    center: body.center.clone(),
    axisX: body.axisX.clone(),
    axisY: body.axisY.clone(),
    major: body.major,
    minor: body.minor,
    // Keep each body's current angle, including hover-induced pauses.
    phase: (body.phase + body.radiansPerSecond * body.elapsedSeconds) % (2 * Math.PI),
    radiansPerSecond: body.radiansPerSecond,
  }]))
}

/**
 * Reuse exactly the old track and phase. Do not deduce axes or eccentricity
 * from the temporarily different leaf camera. Positions must be the snapshot
 * taken at the SAME navigation boundary.
 */
export function restoreOrbitFrame(
  saved: ReadonlyMap<string, SavedOrbit> | undefined,
  visibleIds: readonly string[],
  coordinates: ReadonlyMap<string, Vector3>,
): Map<string, SavedOrbit> | null {
  if (!saved || saved.size !== visibleIds.length ||
    visibleIds.some(id => !saved.has(id) || !coordinates.has(id))) return null
  return new Map(visibleIds.map(id => {
    const pose = saved.get(id)!
    return [id, {
      ...pose,
      world: coordinates.get(id)!.clone(),
      center: pose.center.clone(),
      axisX: pose.axisX.clone(),
      axisY: pose.axisY.clone(),
    }]
  }))
}
