import test from 'node:test'
import assert from 'node:assert/strict'
import { sizeOrderedBrowseBands } from './sizeOrderedBrowseBands.ts'
import { orbitMetrics } from './orbitMetrics.ts'

function sampleGroups(counts: readonly number[]) {
  return counts.map((count,i) => ({
    id: `view-group:${String(counts.length-i).padStart(2,'0')}`,
    source_count:count,
    name: `arbitrary-title-${counts.length-i}`,
  }))
}

test('35 browse groups occupy four ordered belts: smaller inside, larger outside',()=>{
  const counts=[
    14,3,7,10,4,8,3,6,9,5,11,4,3,8,12,5,6,13,
    4,9,6,5,7,10,6,8,3,4,12,7,5,9,3,11,4,
  ]
  const bands=sizeOrderedBrowseBands(sampleGroups(counts))
  assert.equal(bands.length,4)
  assert.deepEqual(bands.map(b=>b.length),[6,8,10,11])
  const all=bands.flat()
  assert.equal(all.length,35)
  assert.deepEqual(all.map(g=>g.source_count),
    [...counts].sort((a,b)=>a-b))
  for(let ring=0;ring<bands.length-1;ring++) {
    const inner=bands[ring],outer=bands[ring+1]
    assert.ok(Math.max(...inner.map(g=>g.source_count)) <=
      Math.min(...outer.map(g=>g.source_count)))
    const innerAvg=inner.reduce((s,g)=>s+g.source_count,0)/inner.length
    const outerAvg=outer.reduce((s,g)=>s+g.source_count,0)/outer.length
    const before=orbitMetrics('group',innerAvg,'browse-orbit-belt')
    const after=orbitMetrics('group',outerAvg,'browse-orbit-belt')
    assert.ok(after.periodSeconds>=before.periodSeconds,
      'outer belts cannot orbit faster than inner belts')
  }
})

test('small groups are never sent to the outer orbit just for their names',()=>{
  const counts=[30,2,24,1,22,3,20,4,18,5,16,6,14,7,12,8,10,9]
  const bands=sizeOrderedBrowseBands(sampleGroups(counts))
  assert.equal(bands.length,2)
  assert.equal(bands[0][0].source_count,1)
  assert.equal(bands.at(-1)?.at(-1)?.source_count,30)
  const reordered=sampleGroups(counts).reverse()
    .map(g=>({...g,name:'completely renamed '+g.id}))
  assert.deepEqual(sizeOrderedBrowseBands(reordered).map(b=>b.map(g=>g.id)),
    bands.map(b=>b.map(g=>g.id)))
})

test('few heterogeneous groups still have an inner and outer band',()=>{
  const bands=sizeOrderedBrowseBands(sampleGroups([12,1,6,3,9]))
  assert.equal(bands.length,2)
  assert.ok(Math.max(...bands[0].map(p=>p.source_count)) <=
    Math.min(...bands[1].map(p=>p.source_count)))
})

test('same-sized groups use stable IDs to break ties',()=>{
  const input=sampleGroups(Array.from({length:26},()=>3))
  assert.deepEqual(
    sizeOrderedBrowseBands(input).map(b=>b.map(p=>p.id)),
    sizeOrderedBrowseBands([...input].reverse()).map(b=>b.map(p=>p.id)),
  )
})

test('all groups are retained for empty, single, or large atlases',()=>{
  assert.deepEqual(sizeOrderedBrowseBands([]),[])
  assert.equal(sizeOrderedBrowseBands(sampleGroups([5])).length,1)
  const many=sampleGroups(Array.from({length:75},(_,i)=>i+1))
  const bands=sizeOrderedBrowseBands(many)
  assert.ok(bands.length<=8)
  assert.deepEqual(bands.flat().map(g=>g.source_count),
    many.map(g=>g.source_count).sort((a,b)=>a-b))
})
