import {describe,it,expect} from 'vitest'
import {parseSSEBlock} from './api'

describe('Agent SSE 解析',()=>{
 it('解析 id、事件名和 JSON 数据',()=>{
  const event=parseSSEBlock('id: 3\nevent: tool_call\ndata: {"step":2,"tool":"route_day"}')
  expect(event).toMatchObject({seq:3,phase:'tool_call',detail:{step:2,tool:'route_day'}})
 })
 it('空块返回 null',()=>expect(parseSSEBlock('')).toBeNull())
})
