export class ApiRequestError extends Error {
  constructor(message:string,public status:number){super(message);this.name="ApiRequestError";}
}

// Read requests may be replayed after a brief outage. Never replay a mutation:
// a lost response does not mean the click or training request was not accepted.
export async function requestApi<T=unknown>(path:string,body:unknown,prefix:string,signal?:AbortSignal):Promise<T> {
  const reading=body===undefined;
  for(let attempt=0;;attempt++) {
    signal?.throwIfAborted();
    let response:Response;
    try {
      response=await fetch(`${prefix}${path}`,{method:reading?"GET":"POST",headers:reading?{}:{"Content-Type":"application/json"},body:reading?undefined:JSON.stringify(body),cache:"no-store",signal});
    } catch(error) {
      if(signal?.aborted)throw error;
      if(reading&&attempt===0){await retryPause(signal);continue;}
      throw new Error(reading?"Connection interrupted. Please retry or use Reconnect.":"Connection interrupted. Your request may already be running. Use Reconnect to check its status before trying again.",{cause:error});
    }
    if(reading&&attempt===0&&[502,503,504].includes(response.status)) {
      await response.body?.cancel();await retryPause(signal);continue;
    }
    if(!response.ok) {
      if(response.status===401)throw new ApiRequestError("Your sign-in session has expired. Reload this page to sign in again.",response.status);
      const error=await response.json().catch(()=>({})) as {detail?:string;error?:string};
      throw new ApiRequestError((typeof error.detail==="string"?error.detail:error.error)||`Request failed (${response.status})`,response.status);
    }
    try{return await response.json() as T;}
    catch(error) {
      if(signal?.aborted)throw error;
      if(error instanceof TypeError&&reading&&attempt===0){await retryPause(signal);continue;}
      throw new Error("The server response was incomplete. Use Reconnect to load the saved result.",{cause:error});
    }
  }
}

function retryPause(signal?:AbortSignal) {
  return new Promise<void>((resolve,reject)=>{
    signal?.throwIfAborted();
    const abort=()=>{clearTimeout(timer);reject(signal?.reason);};
    const timer=setTimeout(()=>{signal?.removeEventListener("abort",abort);resolve();},400);
    signal?.addEventListener("abort",abort,{once:true});
  });
}
