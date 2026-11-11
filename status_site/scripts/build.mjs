import {mkdir,readFile,writeFile,copyFile} from 'node:fs/promises';
await mkdir('dist/server',{recursive:true});
await mkdir('dist/.openai',{recursive:true});
let code=await readFile('worker/index.js','utf8');
for(const [name,path] of [['HTML','worker/index.html'],['CLIENT','worker/client.js'],['SW','worker/sw.js']]) {
  code=`const ${name}=${JSON.stringify(await readFile(path,'utf8'))};\n`+code;
}
for(const size of [192,512]) code='const ICON_'+size+'='+JSON.stringify((await readFile('worker/icon-'+size+'.png')).toString('base64'))+';\n'+code;
await writeFile('dist/server/index.js',code);
await copyFile('.openai/hosting.json','dist/.openai/hosting.json');
