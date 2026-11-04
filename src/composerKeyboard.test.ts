import {describe,it,expect,vi} from 'vitest'
import {handleComposerKeydown} from './composerKeyboard'
function event(overrides:Partial<KeyboardEvent>={},value='我想参观浙工大') {return {key:'Enter',keyCode:13,isComposing:false,shiftKey:false,repeat:false,preventDefault:vi.fn(),currentTarget:{value},...overrides} as unknown as KeyboardEvent}
describe('输入框回车提交',()=>{
 it('正常Enter读取文本框最新内容并阻止默认换行',()=>{const send=vi.fn(),e=event();expect(handleComposerKeydown(e,send)).toBe(true);expect(send).toHaveBeenCalledExactlyOnceWith('我想参观浙工大');expect(e.preventDefault).toHaveBeenCalledOnce()})
 it('中文选词Enter只交给输入法，下一次普通Enter发送',()=>{const send=vi.fn(),composing=event({isComposing:true});expect(handleComposerKeydown(composing,send)).toBe(false);expect(composing.preventDefault).not.toHaveBeenCalled();expect(send).not.toHaveBeenCalled();handleComposerKeydown(event(),send);expect(send).toHaveBeenCalledOnce()})
 it('兼容输入法229键码，不误发送候选文字',()=>{const send=vi.fn();handleComposerKeydown(event({keyCode:229}),send);expect(send).not.toHaveBeenCalled()})
 it('Shift+Enter保持默认换行',()=>{const send=vi.fn(),e=event({shiftKey:true});handleComposerKeydown(e,send);expect(send).not.toHaveBeenCalled();expect(e.preventDefault).not.toHaveBeenCalled()})
 it('按住回车不重复提交，空白不发送',()=>{const send=vi.fn();handleComposerKeydown(event({repeat:true}),send);handleComposerKeydown(event({},'  \n '),send);expect(send).not.toHaveBeenCalled()})
 it('Enter使用最新DOM文字，不依赖可能延后的v-model',()=>{let stale='我想参观';const send=vi.fn((text:string)=>stale=text);handleComposerKeydown(event({},'我想参观浙江工业大学（朝晖校区）'),send);expect(stale).toBe('我想参观浙江工业大学（朝晖校区）')})
})
