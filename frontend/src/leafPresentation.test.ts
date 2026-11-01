import test from 'node:test'
import assert from 'node:assert/strict'
import { leafCaptionDimensions, shouldAnimateDetailFocus } from './leafPresentation.ts'

test('leaf labels are compact in the ordinary nine-point neighborhood', () => {
  const frame=leafCaptionDimensions(false,false,false)
  assert.equal(frame.width,150)
  assert.ok(frame.height<46)
  assert.ok(frame.width<218,
    'a 218px-wide leaf label overpowers its knowledge planet')
})

test('selected and one-hop related captions stay visibly smaller than old focus cards', () => {
  const selected=leafCaptionDimensions(true,false,true)
  const related=leafCaptionDimensions(false,true,true)
  const dimmed=leafCaptionDimensions(false,false,true)
  assert.ok(selected.width<180)
  assert.ok(related.width<150)
  assert.ok(dimmed.width<=128)
  assert.ok(selected.height<48 && related.height<42)
  assert.ok(selected.width>=related.width)
})

test('same-neighborhood selections still animate even when coordinates must be preserved', () => {
  assert.equal(shouldAnimateDetailFocus(true,false,true),true)
  assert.equal(shouldAnimateDetailFocus(false,false,true),false)
  assert.equal(shouldAnimateDetailFocus(true,true,true),false)
  assert.equal(shouldAnimateDetailFocus(true,false,false),false)
})
