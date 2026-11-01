import { Vector3 } from 'three'

/**
 * Recover the single affine map from the canonical XY ellipse to 3D world
 * coordinates after a chapter-entry camera pan/zoom. Never infer separate
 * ring centers or radii from a sparse sample of current planet positions:
 * that turns one orbit into many offset ellipses and invalidates size order.
 */
export type GroupOrbitSample = {
  id: string
  local: Vector3
  world: Vector3
}
export type GroupOrbitDefinition = {
  center: Vector3
  axisX: Vector3
  axisY: Vector3
  major: number
  minor: number
  phase: number
}

export function canonicalGroupOrbitFrames(
  samples: readonly GroupOrbitSample[],
  bands: readonly (readonly string[])[],
  outerA: number,
  outerB: number,
  tilt = -.055,
): Map<string, GroupOrbitDefinition> | null {
  if (samples.length < 3 || !bands.length) return null
  const localCenter = new Vector3()
  const worldCenter = new Vector3()
  for (const point of samples) {
    localCenter.add(point.local)
    worldCenter.add(point.world)
  }
  localCenter.multiplyScalar(1 / samples.length)
  worldCenter.multiplyScalar(1 / samples.length)
  let xx = 0, yy = 0, xy = 0
  const wx = new Vector3(), wy = new Vector3()
  for (const item of samples) {
    const x = item.local.x - localCenter.x
    const y = item.local.y - localCenter.y
    const delta = item.world.clone().sub(worldCenter)
    xx += x * x; yy += y * y; xy += x * y
    wx.addScaledVector(delta, x)
    wy.addScaledVector(delta, y)
  }
  const determinant = xx * yy - xy * xy
  if (determinant < 1e-8) return null
  // World = affineOrigin + basisX * local.x + basisY * local.y
  const basisX = wx.clone().multiplyScalar(yy)
    .addScaledVector(wy, -xy).multiplyScalar(1 / determinant)
  const basisY = wy.clone().multiplyScalar(xx)
    .addScaledVector(wx, -xy).multiplyScalar(1 / determinant)
  const center = worldCenter.clone()
    .addScaledVector(basisX, -localCenter.x)
    .addScaledVector(basisY, -localCenter.y)
  const cos = Math.cos(tilt), sin = Math.sin(tilt)
  const xAxis = basisX.clone().multiplyScalar(cos)
    .addScaledVector(basisY, sin)
  const yAxis = basisY.clone().multiplyScalar(cos)
    .addScaledVector(basisX, -sin)
  const xScale = xAxis.length(), yScale = yAxis.length()
  if (xScale < 1e-8 || yScale < 1e-8) return null
  xAxis.normalize()
  yAxis.normalize()
  const samplesById = new Map(samples.map(s => [s.id,s]))
  const frames = new Map<string, GroupOrbitDefinition>()
  for (let ring = 0; ring < bands.length; ring++) {
    const fraction = bands.length === 1 ? 1 :
      .40 + .60 * ring / (bands.length - 1)
    const a = outerA * fraction, b = outerB * fraction
    for (const id of bands[ring]) {
      const point = samplesById.get(id)
      if (!point) return null
      const u = point.local.x * cos + point.local.y * sin
      const v = -point.local.x * sin + point.local.y * cos
      const phase = Math.atan2(v / b, u / a)
      frames.set(id, {
        center: center.clone(),
        axisX: xAxis.clone(), axisY: yAxis.clone(),
        major: a * xScale, minor: b * yScale, phase,
      })
    }
  }
  if (frames.size !== samples.length) return null
  return frames
}
