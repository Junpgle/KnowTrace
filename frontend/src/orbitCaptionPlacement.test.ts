import test from 'node:test'
import assert from 'node:assert/strict'
import { placeOrbitCaptions, boxesOverlap } from './orbitCaptionPlacement.ts'
import type { OrbitCaptionItem } from './orbitCaptionPlacement.ts'

const bounds={left:15,top:15,right:1545,bottom:935}
const items:OrbitCaptionItem[]=[
  {id:'chapter-303',x:815,y:266,radius:31,width:155,height:46,priority:303},
  {id:'chapter-175',x:865,y:385,radius:21,width:155,height:46,priority:175},
  {id:'chapter-106',x:814,y:469,radius:16,width:155,height:46,priority:106},
  {id:'chapter-111',x:905,y:535,radius:16,width:155,height:46,priority:111},
  {id:'chapter-216',x:1095,y:605,radius:25,width:155,height:46,priority:216},
  {id:'chapter-255',x:366,y:638,radius:29,width:155,height:46,priority:255},
  {id:'chapter-141',x:625,y:515,radius:19,width:155,height:46,priority:141},
]
const obstacles=[
  {left:15,top:15,right:570,bottom:155},
  {left:1375,top:17,right:1545,bottom:98},
  {left:15,top:780,right:234,bottom:935},
]

test('all 7 chapter names are displayed when there is enough screen space',()=>{
  const placed=placeOrbitCaptions(items,bounds,obstacles)
  assert.equal(placed.length,items.length,
    'do not hide a caption solely because the above-planet slot is busy')
  for(const place of placed){
    assert.ok(place.box.left>=bounds.left && place.box.right<=bounds.right)
    assert.ok(place.box.top>=bounds.top && place.box.bottom<=bounds.bottom)
    assert.ok(!obstacles.some(o=>boxesOverlap(o,place.box)))
    assert.ok(!placed.some(other=>other.id!==place.id &&
      boxesOverlap(other.box,place.box,4)))
  }
})

test('a blocked top slot uses empty side space rather than hiding a label',()=>{
  const lonely:OrbitCaptionItem[]=[{
    id:'chapter',x:780,y:430,width:166,height:48,radius:32,priority:100,
  }]
  const topBlocked={left:650,right:920,top:280,bottom:398}
  const bottomBlocked={left:650,right:920,top:465,bottom:630}
  const placed=placeOrbitCaptions(lonely,bounds,[topBlocked,bottomBlocked])
  assert.equal(placed.length,1)
  assert.ok(!boxesOverlap(placed[0].box,topBlocked))
  assert.ok(!boxesOverlap(placed[0].box,bottomBlocked))
  assert.notEqual(placed[0].dx,0)
})

test('a valid old slot is sticky as planets move a few pixels',()=>{
  const prior=placeOrbitCaptions(items,bounds,obstacles)
  const slots=Object.fromEntries(prior.map(p=>[p.id,{dx:p.dx,dy:p.dy}]))
  const moved=items.map((p,i)=>({...p,x:p.x+((i%3)-1)*2,y:p.y+((i%2)*2-1)}))
  const next=placeOrbitCaptions(moved,bounds,obstacles,slots)
  assert.equal(next.length,items.length)
  for(const item of next){
    assert.deepEqual({dx:item.dx,dy:item.dy},slots[item.id])
  }
})

test('additional candidates do not cause labels to flee the atlas',()=>{
  const p:OrbitCaptionItem={id:'dense',x:700,y:430,radius:20,width:150,height:45,priority:100}
  const placed=placeOrbitCaptions([p],bounds,[])
  assert.equal(placed.length,1)
  assert.ok(Math.hypot(placed[0].dx,placed[0].dy)<160)
})
