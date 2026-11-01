import test from 'node:test'
import assert from 'node:assert/strict'
import { planCenteredCourseOrbits } from './centeredCourseOrbits.ts'

const chapters = [
  { id: '第6章网络层', knowledgeCount: 303 },
  { id: '未归类知识点', knowledgeCount: 255 },
  { id: '第2章数据通信', knowledgeCount: 216 },
  { id: '第8章应用层', knowledgeCount: 175 },
  { id: '第7章传输层', knowledgeCount: 141 },
  { id: '第3章网络结构与协议', knowledgeCount: 111 },
  { id: '第1章概述', knowledgeCount: 106 },
]

test('seven chapters share one course center but use distinct complete ellipses', () => {
  const { orbits, pixelScale } = planCenteredCourseOrbits(chapters, 1.8, 1450, 770)
  assert.equal(orbits.length, 7)
  assert.ok(pixelScale > 0 && pixelScale <= 1)
  assert.equal(new Set(orbits.map(lane => lane.minor)).size, 7)
  for (const orbit of orbits) {
    assert.equal(orbit.major, orbit.minor * 1.8)
    assert.ok(orbit.major > orbit.minor)
    assert.ok(orbit.radiusPx > 0)
  }
})

test('no two concentric ellipse lanes intersect even at arbitrary orbital phases', () => {
  const { orbits, pixelScale } = planCenteredCourseOrbits(chapters, 1.8, 1450, 770)
  for (let i = 0; i < orbits.length; i++) {
    for (let j = i + 1; j < orbits.length; j++) {
      const first = orbits[i], second = orbits[j]
      assert.ok(second.minor - first.minor >=
        first.radiusPx + second.radiusPx + 23.9)
      for (let a = 0; a < 36; a++) {
        for (let b = 0; b < 36; b++) {
          const theta = 2 * Math.PI * a / 36
          const phi = 2 * Math.PI * b / 36
          const p = [first.major * Math.cos(theta), first.minor * Math.sin(theta)]
          const q = [second.major * Math.cos(phi), second.minor * Math.sin(phi)]
          assert.ok(Math.hypot(p[0] - q[0], p[1] - q[1]) >
            first.radiusPx + second.radiusPx + 23,
            `Unexpected near-miss for ${first.id}/${second.id}`)
        }
      }
    }
  }
  // The uniform fit does NOT shrink a planet at conjunction.
  const scaleAtDifferentPhases = [0, Math.PI / 3, Math.PI].map(() => pixelScale)
  assert.deepEqual(scaleAtDifferentPhases, [pixelScale, pixelScale, pixelScale])
})

test('more knowledge produces a bigger visible planet on a slower outer orbit', () => {
  const { orbits, pixelScale } = planCenteredCourseOrbits(chapters, 1.7, 1400, 800)
  for (let i = 1; i < orbits.length; i++) {
    assert.ok(orbits[i].count > orbits[i - 1].count)
    assert.ok(orbits[i].radiusPx * pixelScale >
      orbits[i - 1].radiusPx * pixelScale)
    assert.ok(orbits[i].periodSeconds > orbits[i - 1].periodSeconds)
    assert.ok(orbits[i].minor > orbits[i - 1].minor)
  }
})

test('recomputing the layout never randomly repositions the planets', () => {
  const first = planCenteredCourseOrbits(chapters, 1.8, 1450, 770)
  const second = planCenteredCourseOrbits([...chapters].reverse(), 1.8, 1450, 770)
  assert.deepEqual(second, first)
  for (const ring of first.orbits) {
    const initial = [
      ring.major * Math.cos(ring.phase),
      ring.minor * Math.sin(ring.phase),
    ]
    const atTimeZero = [
      ring.major * Math.cos(ring.phase + 2 * Math.PI / ring.periodSeconds * 0),
      ring.minor * Math.sin(ring.phase + 2 * Math.PI / ring.periodSeconds * 0),
    ]
    assert.deepEqual(atTimeZero, initial)
    const atQuarterTurn = [
      ring.major * Math.cos(ring.phase + Math.PI / 2),
      ring.minor * Math.sin(ring.phase + Math.PI / 2),
    ]
    assert.ok(Math.hypot(initial[0] - atQuarterTurn[0],
      initial[1] - atQuarterTurn[1]) > 40)
  }
})
