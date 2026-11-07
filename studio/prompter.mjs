// Reuse the installed Camera Hub connection. Mode enum is from its installed plugin:
// Text=1, Chat=2, Extended (Screen)=3. Never alter power or other properties.
import {connectCameraHub} from 'file:///C:/Users/Anigma%20PC/Documents/Stream%20Deck%20Studio/launchers/camera-hub-rpc.mjs';
const c=await connectCameraHub();try{
 const devices=await c.call('getAvailablePrompter');
 if(!devices.some(d=>d.isPrompter))throw Error('Prompter is not connected');
 let p=await c.call('getPrompterProperty',{propertyID:1});
 if(!p.isSupported)throw Error('Prompter mode is unavailable');
 if(process.argv[2]==='screen'&&p.value!==3){await c.call('setPrompterProperty',{propertyID:1,value:3});p=await c.call('getPrompterProperty',{propertyID:1});}
 console.log(JSON.stringify({connected:true,screen_mode:p.value===3,mode:p.value,device_count:devices.filter(d=>d.isPrompter).length}));
}finally{c.close();}
