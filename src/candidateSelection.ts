export interface Mention {text:string;role:'visit'|'destination'|'origin'}
export const mentionKey=(mention:Mention)=>`${mention.role}:${mention.text}`
// Preserve independent manual and mention references when changing a resolution.
export function candidateSelection(manual:string[],mentions:Mention[],chosen:Record<string,string>,endpoints:(string|null|undefined)[]){
 return [...new Set([...manual,...mentions.filter(m=>m.role==='visit').map(m=>chosen[mentionKey(m)]).filter((id):id is string=>!!id)])].filter(id=>!endpoints.includes(id))
}
