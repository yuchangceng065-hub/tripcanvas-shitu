import {it,expect} from 'vitest'
import {candidateSelection,mentionKey,mergeImportedOptional,type Mention} from './candidateSelection'
const visit:Mention={text:'浙江工业大学',role:'visit'}
const other:Mention={text:'另一个提及',role:'visit'}
it('改选校区只保留新选择，反复切换不累积',()=>{
 const chosen:Record<string,string>={[mentionKey(visit)]:'A'}
 expect(candidateSelection([],[visit],chosen,[])).toEqual(['A'])
 chosen[mentionKey(visit)]='B';expect(candidateSelection([],[visit],chosen,[])).toEqual(['B'])
 chosen[mentionKey(visit)]='A';expect(candidateSelection([],[visit],chosen,[])).toEqual(['A'])
})
it('保留其他提及和手动添加对旧地点的独立引用',()=>{
 const chosen={[mentionKey(visit)]:'B',[mentionKey(other)]:'A'}
 expect(candidateSelection([],[visit,other],chosen,[])).toEqual(['B','A'])
 expect(candidateSelection(['A'],[visit],chosen,[])).toEqual(['A','B'])
})
it('同名起点和游览提及分离，起终点不重复加入游览候选',()=>{
 const origin:Mention={...visit,role:'origin'}
 expect(mentionKey(origin)).not.toBe(mentionKey(visit))
 expect(candidateSelection(['A'],[visit,origin],{[mentionKey(visit)]:'A'},['A'])).toEqual([])
})
it('重新解析清空提及选择后，只保留手动候选',()=>expect(candidateSelection(['manual'],[visit],{},[])).toEqual(['manual']))
it('攻略可选不降级已有必去，新必去升级已有可选',()=>{
 expect(mergeImportedOptional(['q','m'],['m'],['q','m','s'],['q','s'])).toEqual(['s'])
})
