import test from 'node:test'
import assert from 'node:assert/strict'
import { orbitMetrics } from './orbitMetrics.ts'

test('three versus fourteen knowledge points: visibly larger AND slower group', () => {
  const small = orbitMetrics('group', 3, 'view-group:small')
  const large = orbitMetrics('group', 14, 'view-group:large')
  assert.ok(large.idealRadiusPx / small.idealRadiusPx > 1.8)
  assert.ok(large.periodSeconds / small.periodSeconds > 1.9)
})

test('realistic chapter counts 106 versus 303 yield obviously distinct planets', () => {
  const small = orbitMetrics('chapter', 106, 'ch:overview')
  const large = orbitMetrics('chapter', 303, 'ch:network')
  assert.ok(large.idealRadiusPx / small.idealRadiusPx > 1.8)
  assert.ok(large.periodSeconds / small.periodSeconds > 1.9)
})

test('one extra knowledge point must never make a planet smaller or faster, across IDs', () => {
  const ids = ['A', 'B', 'C', '第1章', 'view-group:42']
  for (const kind of ['chapter', 'group'] as const) {
    for (let n = 0; n < 650; n++) {
      for (const id of ids) {
        const before = orbitMetrics(kind, n, id)
        const after = orbitMetrics(kind, n + 1, ids[(ids.indexOf(id) + 1) % ids.length])
        assert.ok(after.idealRadiusPx >= before.idealRadiusPx,
          `${kind}: count ${n+1} should not shrink relative to ${n}`)
        assert.ok(after.periodSeconds > before.periodSeconds,
          `${kind}: count ${n+1} should orbit strictly more slowly than ${n}`)
      }
    }
  }
})

test('same planet retains a stable but individualized orbital period', () => {
  const one = orbitMetrics('group', 9, 'view-group:one')
  assert.deepEqual(one, orbitMetrics('group', 9, 'view-group:one'))
  const two = orbitMetrics('group', 9, 'view-group:two')
  assert.notEqual(one.periodSeconds, two.periodSeconds)
  assert.equal(one.idealRadiusPx, two.idealRadiusPx)
})

test('bad counts are bounded, never create NaN or negative radii', () => {
  for (const n of [-4, Number.NaN, Number.POSITIVE_INFINITY]) {
    for (const kind of ['chapter', 'group'] as const) {
      const metric = orbitMetrics(kind, n, 'bad')
      assert.ok(Number.isFinite(metric.idealRadiusPx))
      assert.ok(Number.isFinite(metric.periodSeconds))
      assert.ok(metric.periodSeconds > 0)
    }
  }
})
