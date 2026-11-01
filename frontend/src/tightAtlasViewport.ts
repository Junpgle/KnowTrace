/**
 * Tight initial framing for an animated orbit atlas. Samples must cover the
 * COMPLETE paths, not just today's planet positions, so every future phase
 * remains visible. Perspective is evaluated in screen pixels at the exact
 * proposed camera distance: no repeated spherical fit or world-unit padding.
 *
 * This is only for a course overview without detail drawers. Other hierarchy
 * views retain the safe-area camera and relation-graph fitting behavior.
 */
export type AtlasPoint3 = { x:number;y:number;z:number }
export type TightAtlasFrame = {
  points: readonly AtlasPoint3[]
  center: AtlasPoint3
  eyeDirection: AtlasPoint3
  viewportWidth: number
  viewportHeight: number
  fovDegrees: number
  aspect: number
}

const dot=(a:AtlasPoint3,b:AtlasPoint3)=>a.x*b.x+a.y*b.y+a.z*b.z
const cross=(a:AtlasPoint3,b:AtlasPoint3):AtlasPoint3=>({
  x:a.y*b.z-a.z*b.y,y:a.z*b.x-a.x*b.z,z:a.x*b.y-a.y*b.x,
})
const normalized=(a:AtlasPoint3):AtlasPoint3=>{
  const m=Math.max(1e-9,Math.hypot(a.x,a.y,a.z))
  return {x:a.x/m,y:a.y/m,z:a.z/m}
}
export function projectAtlasBounds(frame:TightAtlasFrame,distance:number) {
  const forward=normalized(frame.eyeDirection)
  let right=cross({x:0,y:1,z:0},forward)
  if(Math.hypot(right.x,right.y,right.z)<1e-6)right={x:1,y:0,z:0}
  right=normalized(right)
  const up=normalized(cross(forward,right))
  const tanV=Math.tan(frame.fovDegrees*Math.PI/360)
  const tanH=tanV*frame.aspect
  const w=frame.viewportWidth,h=frame.viewportHeight
  let left=Infinity,rightEdge=-Infinity,top=Infinity,bottom=-Infinity
  for(const p of frame.points){
    const relative={
      x:p.x-frame.center.x,y:p.y-frame.center.y,z:p.z-frame.center.z,
    }
    const depth=distance-dot(relative,forward)
    if(depth<=.1)return null
    const x=w/2+(dot(relative,right)/depth/tanH)*w/2
    const y=h/2-(dot(relative,up)/depth/tanV)*h/2
    left=Math.min(left,x);rightEdge=Math.max(rightEdge,x)
    top=Math.min(top,y);bottom=Math.max(bottom,y)
  }
  return frame.points.length?{left,right:rightEdge,top,bottom}:null
}

export function tightAtlasDistance(
  frame:TightAtlasFrame,
  fallbackDistance:number,
):number{
  if(!frame.points.length||frame.viewportWidth<200||frame.viewportHeight<160)
    return fallbackDistance
  const w=frame.viewportWidth,h=frame.viewportHeight
  // Card captions are ~130-170px wide. Allow half a caption outside the
  // orbital path without clipping it; the page header only occupies a
  // CORNER, not the full width of the canvas.
  const marginX=Math.max(85,w*.065)
  const marginY=Math.max(92,h*.14)
  const fits=(distance:number)=>{
    const box=projectAtlasBounds(frame,distance)
    return !!box &&
      box.left>=marginX && box.right<=w-marginX &&
      box.top>=marginY && box.bottom<=h-marginY
  }
  let high=Math.max(80,fallbackDistance)
  for(let i=0;i<14&&!fits(high);i++)high*=1.5
  if(!fits(high))return fallbackDistance
  let low=Math.max(20,Math.min(70,high*.04))
  for(let i=0;i<32;i++){
    const mid=(low+high)/2
    if(fits(mid))high=mid
    else low=mid
  }
  return high
}
