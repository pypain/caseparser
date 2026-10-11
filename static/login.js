document.querySelector('#login-form').addEventListener('submit',async e=>{
  e.preventDefault();const b=e.target.querySelector('button'),err=document.querySelector('#login-error');b.disabled=true;err.textContent='';
  try{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:new FormData(e.target).get('password')})});const body=await r.json();if(!r.ok)throw new Error(body.error);location.assign('/prices');}
  catch(e){err.textContent=e.message;}finally{b.disabled=false;}
});
