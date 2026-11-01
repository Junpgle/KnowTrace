import test from 'node:test'
import assert from 'node:assert/strict'
import { orbitMetrics } from './orbitMetrics.ts'
import { planIndependentOrbitLanes } from './independentOrbitLanes.ts'

const chapters = [
  ['网络层', 303], ['应用层', 175], ['数据通信', 216],
  ['概述', 106], ['传输层', 141], ['局域网', 255], ['结构与协议', 111],
] as const

test('seven differently sized chapters occupy seven distinct elliptical paths', () => {
  const anchors = chapters.map(([id,count],i) => {
    const angle=(i+.15)*2*Math.PI/chapters.length
    return {
      id,
      x: 510*Math.cos(angle), y: 325*Math.sin(angle),
      radius:orbitMetrics('chapter',count,id).idealRadiusPx,
    }
  })
  const lanes=planIndependentOrbitLanes(anchors,38,18)
  assert.equal(lanes.length,7)
  for (const lane of lanes) {
    assert.ok(lane.major>0, `planet ${lane.id} should revolve in a private lane`)
    assert.equal(lane.minor,lane.major*.60)
  }
  assert.equal(new Set(lanes.map(l => l.id)).size,7)
  assert.equal(new Set(lanes.map(l => `${l.centerX}:${l.centerY}`)).size,7)
  // Stress every pair of independent orbital phases, not just their
  // initial positions. Slower large planets may be overtaken, but never
  // share a path or intersect a neighbor's disk.
  for (let i=0;i<lanes.length;i++)for(let j=i+1;j<lanes.length;j++) {
    for(let ai=0;ai<30;ai++)for(let aj=0;aj<30;aj++) {
      const u=ai*2*Math.PI/30,v=aj*2*Math.PI/30
      const a=lanes[i],b=lanes[j]
      const dx=(a.centerX+a.major*Math.cos(u))-
        (b.centerX+b.major*Math.cos(v))
      const dy=(a.centerY+a.minor*Math.sin(u))-
        (b.centerY+b.minor*Math.sin(v))
      const safe=anchors[i].radius+anchors[j].radius+18
      assert.ok(Math.hypot(dx,dy)>=safe-.00001,
        `${a.id} and ${b.id} collide at phases ${ai},${aj}`)
    }
  }
})

test('lane planning does not change a large body radius, even in a crowded map', () => {
  const anchors=[
    {id:'large',x:0,y:0,radius:58},
    {id:'small',x:107,y:0,radius:14},
  ]
  const lanes=planIndependentOrbitLanes(anchors,40,14)
  assert.ok(lanes[0].major>=0)
  assert.ok(lanes[1].major>=0)
  assert.equal(anchors[0].radius,58)
  assert.equal(anchors[1].radius,14)
  const second=planIndependentOrbitLanes(anchors,40,14)
  assert.deepEqual(lanes,second)
})

test('group planets also have private lanes even when sharing a visual belt', () => {
  const groupCount=12
  const anchors=Array.from({length:groupCount},(_,i)=>{
    const count=3+i
    const id=`group-${i}`
    const theta=2*Math.PI*i/groupCount
    return {
      id,x:360*Math.cos(theta),y:240*Math.sin(theta),
      radius:orbitMetrics('group',count,id).idealRadiusPx,
    }
  })
  const lanes=planIndependentOrbitLanes(anchors,20,12)
  assert.equal(lanes.length,groupCount)
  assert.ok(lanes.every(l=>l.major>0))
  assert.equal(new Set(lanes.map(l=>l.centerX+':'+l.centerY)).size,groupCount)
})
