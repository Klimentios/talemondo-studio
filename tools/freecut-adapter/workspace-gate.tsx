import { useEffect, useState } from 'react'
import { setWorkspaceRoot } from '@/infrastructure/storage/workspace-fs/root'
import { bootstrapWorkspace } from '@/infrastructure/storage/workspace-fs/bootstrap'

// Private browser storage: no directory chooser and no access to arbitrary user folders.
export function WorkspaceGate({ children }: { children: React.ReactNode }) {
 const [ready,setReady]=useState(false)
 const [error,setError]=useState('')
 useEffect(()=>{let active=true;void(async()=>{
  const storage=await navigator.storage.getDirectory()
  const root=await storage.getDirectoryHandle('talemondo-workspace',{create:true})
  setWorkspaceRoot(root)
  await bootstrapWorkspace(root)
  if(active)setReady(true)
 })().catch(e=>{if(active)setError(String(e))});return()=>{active=false}},[])
 if(error)return <main style={{padding:32,color:'#eee',background:'#101012'}}>Не удалось открыть хранилище монтажа: {error}</main>
 if(!ready)return <main style={{padding:32,color:'#eee',background:'#101012'}}>Открываем монтаж TaleMondo…</main>
 return <>{children}</>
}
