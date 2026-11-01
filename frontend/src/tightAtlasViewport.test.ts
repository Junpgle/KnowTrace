import test from 'node:test'
import assert from 'node:assert/strict'
import { tightAtlasDistance, projectAtlasBounds } from './tightAtlasViewport.ts'
import type { TightAtlasFrame } from './tightAtlasViewport.ts'

function makeOrbit(major:number,minor:number,count=64){
  return Array.from({length:count},(_,i)=>{
    const t=i*Math.PI*2/count
    return {x:major*Math.cos(t),y:0,z:minor*Math.sin(t)}
  })
}
function frame(width:number,height:number):TightAtlasFrame{
  return {
    points:[
      ...makeOrbit(720,320),
      ...makeOrbit(550,240),
      ...makeOrbit(350,160),
      {x:0,y:0,z:0},
    ],
    center:{x:0,y:0,z:0},
    eyeDirection:{x:0,y:1,z:.24},
    viewportWidth:width,viewportHeight:height,
    fovDegrees:57,aspect:width/height,
  }
}
test('desktop course orbit atlas occupies available screen without clipping',()=>{
  const f=frame(1540,820)
  const previous=1850
  const distance=tightAtlasDistance(f,previous)
  assert.ok(distance<previous*.70,'the old overly conservative zoom must be closer')
  const box=projectAtlasBounds(f,distance)!
  const width=box.right-box.left,height=box.bottom-box.top
  assert.ok(width>1540*.78,'whole orbit should fill most of desktop width')
  assert.ok(height>820*.59,'whole orbit should fill more vertical space')
  assert.ok(box.left>=100 && box.right<=1440)
  assert.ok(box.top>=110 && box.bottom<=710)
})

test('all future orbital phases stay in view using complete trajectories',()=>{
  const f=frame(1540,820)
  const fitted=tightAtlasDistance(f,1800)
  for(let i=0;i<360;i++){
    const t=i*Math.PI*2/360
    const current={...f,points:[
      {x:720*Math.cos(t),y:0,z:320*Math.sin(t)},
      {x:550*Math.cos(t*1.7),y:0,z:240*Math.sin(t*1.7)},
    ]}
    const box=projectAtlasBounds(current,fitted)
    assert.ok(box && box.left>=99 && box.right<=1441 &&
      box.top>=109 && box.bottom<=711)
  }
})

test('viewport resize produces a new bounded zoom rather than a fixed factor',()=>{
  const desktop=frame(1540,820)
  const narrower=frame(900,760)
  const d1=tightAtlasDistance(desktop,1850)
  const d2=tightAtlasDistance(narrower,1850)
  assert.ok(d2>d1)
  const b=projectAtlasBounds(narrower,d2)!
  assert.ok(b.left>=85 && b.right<=815)
  assert.ok(b.top>=106 && b.bottom<=654)
})

test('empty or too-small viewports retain a safe fallback',()=>{
  const f=frame(1540,820)
  assert.equal(tightAtlasDistance({...f,points:[]},540),540)
  assert.equal(tightAtlasDistance({...f,viewportWidth:80},540),540)
})
