import test from 'node:test'
import assert from 'node:assert/strict'
import { Vector3 } from 'three'
import { canonicalGroupOrbitFrames } from './groupOrbitGeometry.ts'
import { sizeOrderedBrowseBands } from './sizeOrderedBrowseBands.ts'

const tilt=-.055
const groups=[
  ...Array.from({length:17},(_,i)=>({id:`small-${i}`,source_count:3})),
  ...Array.from({length:10},(_,i)=>({id:`middle-${i}`,source_count:8+i%3})),
  ...Array.from({length:7},(_,i)=>({id:`big-${i}`,source_count:13+i%2})),
]

function fixture(){
  const bands=sizeOrderedBrowseBands(groups)
  const outerB=Math.max(245,100+bands.length*55)
  const outerA=outerB*1.7
  const right=new Vector3(.91,.10,.32).normalize()
  const up=new Vector3(-.20,.96,.05).normalize()
  const center=new Vector3(180,47,-330)
  const scale=.66
  const samples=[]
  for(let band=0;band<bands.length;band++){
    const factor=bands.length===1?1:.40+.60*band/(bands.length-1)
    const a=outerA*factor,b=outerB*factor
    for(let i=0;i<bands[band].length;i++){
      const node=bands[band][i]
      const theta=i*Math.PI*2/bands[band].length+.07*band
      const x=a*Math.cos(theta),y=b*Math.sin(theta)
      const local=new Vector3(
        x*Math.cos(tilt)-y*Math.sin(tilt),
        x*Math.sin(tilt)+y*Math.cos(tilt),0)
      const world=center.clone().addScaledVector(right,local.x*scale)
        .addScaledVector(up,local.y*scale)
      samples.push({id:node.id,local,world})
    }
  }
  return {bands,outerA,outerB,center,samples}
}

test('34 group planets stay on shared-centered, ascending orbital belts',()=>{
  const {bands,outerA,outerB,center,samples}=fixture()
  const frames=canonicalGroupOrbitFrames(samples,
    bands.map(b=>b.map(n=>n.id)),outerA,outerB)
  assert.ok(frames)
  assert.equal(frames.size,34)
  const radii=[]
  for(let ring=0;ring<bands.length;ring++){
    const orbit=frames.get(bands[ring][0].id)!
    radii.push(orbit.minor)
    // NO private center offsets: all rings share the same center at all times.
    for(const node of bands[ring]){
      const body=frames.get(node.id)!
      assert.ok(body.center.distanceTo(center)<1e-5)
      assert.ok(Math.abs(body.major-orbit.major)<1e-5)
      assert.ok(Math.abs(body.minor-orbit.minor)<1e-5)
      const initial=body.center.clone()
        .addScaledVector(body.axisX,body.major*Math.cos(body.phase))
        .addScaledVector(body.axisY,body.minor*Math.sin(body.phase))
      const actual=samples.find(sample=>sample.id===node.id)!.world
      assert.ok(initial.distanceTo(actual)<1e-5,
        `${node.id} must NOT jump on first animation frame`)
    }
  }
  for(let j=1;j<radii.length;j++)
    assert.ok(radii[j]>radii[j-1],
      `outer ring ${j} must be strictly larger than inner ${j-1}`)
})

test('inner/outer ordering survives every possible revolution phase',()=>{
  const {bands,outerA,outerB,samples}=fixture()
  const frames=canonicalGroupOrbitFrames(samples,
    bands.map(b=>b.map(n=>n.id)),outerA,outerB)!
  const all=bands.flat()
  const maxInner=bands.slice(0,-1).map(b=>Math.max(...b.map(g=>g.source_count)))
  const minOuter=bands.slice(1).map(b=>Math.min(...b.map(g=>g.source_count)))
  assert.ok(maxInner.every((x,i)=>x<=minOuter[i]))
  for(let phase=0;phase<360;phase++){
    const t=phase*Math.PI/180
    for(const node of all){
      const ring=bands.findIndex(b=>b.some(g=>g.id===node.id))
      const body=frames.get(node.id)!
      const location=body.center.clone()
        .addScaledVector(body.axisX,body.major*Math.cos(body.phase+t))
        .addScaledVector(body.axisY,body.minor*Math.sin(body.phase+t))
      const relative=location.sub(body.center)
      const radial=Math.hypot(
        relative.dot(body.axisX)/body.major,
        relative.dot(body.axisY)/body.minor)
      assert.ok(Math.abs(radial-1)<1e-7)
      if (ring>0) {
        const inner=frames.get(bands[ring-1][0].id)!
        assert.ok(body.minor>inner.minor)
      }
    }
  }
})

test('invalid / incomplete samples fail closed instead of inventing offset orbits',()=>{
  const {bands,outerA,outerB,samples}=fixture()
  const ids=bands.map(b=>b.map(n=>n.id))
  assert.equal(canonicalGroupOrbitFrames(samples.slice(0,2),ids,outerA,outerB),null)
  assert.equal(canonicalGroupOrbitFrames(samples.slice(1),ids,outerA,outerB),null)
})
