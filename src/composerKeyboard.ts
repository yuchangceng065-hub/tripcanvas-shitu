// Read the actual textarea value: Vue defers v-model updates during Chinese composition.
export function handleComposerKeydown(event:KeyboardEvent,submit:(text:string)=>void):boolean {
 if(event.key!=='Enter'||event.shiftKey||event.isComposing||event.keyCode===229||event.repeat)return false
 event.preventDefault()
 const text=(event.currentTarget as HTMLTextAreaElement|null)?.value??''
 if(!text.trim())return false
 submit(text)
 return true
}
