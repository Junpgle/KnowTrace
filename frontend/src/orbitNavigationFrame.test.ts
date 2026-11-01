import test from 'node:test'
import assert from 'node:assert/strict'
import { Vector3 } from 'three'
import { orbitPosition, restoreOrbitFrame, snapshotOrbitFrame } from './orbitNavigationFrame.ts'
import type { SavedOrbit } from './orbitNavigationFrame.ts'

const EPSILON=1e-7
const close=(a:Vector3,b:Vector3)=>assert.ok(
  a.distanceTo(b)<EPSILON,
  `Expected orbit position ${a.toArray()} to equal ${b.toArray()}`,
)

function exampleGroupOrbits() {
  // 26 groups along four independent, camera-facing elliptic belts. They are
  // converted into frozen geometry before descending to a concept detail.
  const up=new Vector3(.15,.96,.24).normalize()
  const right=new Vector3(.98,-.17,.03).normalize()
  const baseCenter=new Vector3(70,25,-130)
  const counts=[5,6,7,8]
  const result=[]
  let id=0
  for(let band=0;band<counts.length;band++) {
    const n=counts[band]
    const major=70+band*52,minor=45+band*32
    for(let i=0;i<n;i++) {
      const phase=2*Math.PI*(i+.07*band)/n
      const center=baseCenter.clone()
      const world=center.clone()
        .addScaledVector(right,major*Math.cos(phase))
        .addScaledVector(up,minor*Math.sin(phase))
      result.push({
        id:`view-group:chapter8:${id++}`, world,
        center,axisX:right,axisY:up,major,minor,phase,
        radiansPerSecond:2*Math.PI/(31+band*8),
        elapsedSeconds:8.2+band*.75,
      })
    }
  }
  return result
}

test('chapter group returns to exact old ellipses despite a rotated child camera',()=>{
  const first=exampleGroupOrbits()
  const saved=snapshotOrbitFrame(first)
  const coordinates=new Map([...saved].map(([id,pose])=>
    [id,orbitPosition(pose)] as const))
  const oldCameraRight=new Vector3(1,0,0)
  const childCameraRight=new Vector3(.37,.24,.89)
  assert.ok(oldCameraRight.distanceTo(childCameraRight)>.1)
  // The child camera is deliberately different, but is never consulted.
  const restored=restoreOrbitFrame(saved,first.map(p=>p.id),coordinates)
  assert.ok(restored)
  assert.equal(restored.size,26)
  for(const original of first) {
    const before=saved.get(original.id)!
    const restoredPose: SavedOrbit=restored.get(original.id)!
    close(before.center,restoredPose.center)
    close(before.axisX,restoredPose.axisX)
    close(before.axisY,restoredPose.axisY)
    assert.equal(before.major,restoredPose.major)
    assert.equal(before.minor,restoredPose.minor)
    assert.equal(before.phase,restoredPose.phase)
    close(restoredPose.world,orbitPosition(restoredPose))
    // A subsequent orbit tick must keep the same TRACK, not a newly
    // inferred ellipse with a different tilt or center.
    close(orbitPosition(before,before.phase+.16),
      orbitPosition(restoredPose,restoredPose.phase+.16))
  }
})

test('saved orbital positions and velocities do not alias live world vectors',()=>{
  const planets=exampleGroupOrbits()
  const frozen=snapshotOrbitFrame(planets)
  const old=frozen.get(planets[0].id)!
  const originalCenter=old.center.clone()
  planets[0].world.set(999,999,999)
  planets[0].center.set(-999,0,0)
  planets[0].axisX.set(0,0,1)
  close(old.center,originalCenter)
  assert.notEqual(old.world.x,999)
  const coords=new Map([...frozen].map(([id,pose])=>
    [id,pose.world.clone()] as const))
  const restored=restoreOrbitFrame(frozen,[...frozen.keys()],coords)!
  restored.get(planets[0].id)!.center.set(123,123,123)
  close(frozen.get(planets[0].id)!.center,originalCenter)
})

test('return restores the CURRENT orbital phase, not the initial angle',()=>{
  const body=exampleGroupOrbits()[0]
  const saved=snapshotOrbitFrame([body]).get(body.id)!
  const expected=(body.phase+body.elapsedSeconds*body.radiansPerSecond)%(2*Math.PI)
  assert.equal(saved.phase,expected)
  const position=orbitPosition(saved)
  const restored=restoreOrbitFrame(
    new Map([[body.id,saved]]),[body.id],new Map([[body.id,position]]),
  )!.get(body.id)!
  close(restored.world,position)
  close(orbitPosition(restored),position)
})

test('a different chapter with mismatched group IDs cannot reuse the old orbit',()=>{
  const saved=snapshotOrbitFrame(exampleGroupOrbits())
  const coordinates=new Map([...saved].map(([id,pose])=>[id,pose.world]))
  assert.equal(restoreOrbitFrame(saved,['view-group:chapter7:0'],coordinates),null)
  assert.equal(restoreOrbitFrame(saved,[...saved.keys()],new Map()),null)
  assert.equal(restoreOrbitFrame(undefined,[],coordinates),null)
})
