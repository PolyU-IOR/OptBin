#define _POSIX_C_SOURCE 200809L
#include "internal.h"
#include "optbin_hdf5.h"
#include <hdf5.h>
#include <stdlib.h>
#include <limits.h>
#include <unistd.h>
#include <zlib.h>

#define CACHE_BYTES (1024u*1024u)
static int bad(char *e,size_t n,const char *s){if(e&&n)snprintf(e,n,"%s",s);return -1;}
const char *optb_h5_version(void){return H5_VERS_INFO;}
static hid_t disk_type(unsigned t){return t==OPTB_U32?H5T_STD_U32LE:t==OPTB_U64?H5T_STD_U64LE:t==OPTB_F64?H5T_IEEE_F64LE:H5T_STD_U8LE;}
static hid_t mem_type(unsigned t){return t==OPTB_U32?H5T_NATIVE_UINT32:t==OPTB_U64?H5T_NATIVE_UINT64:t==OPTB_F64?H5T_NATIVE_DOUBLE:H5T_NATIVE_UCHAR;}
static hid_t dataset(hid_t f,const char *name,hid_t type,int rank,const hsize_t *dims,int gzip){
    hid_t space=H5Screate_simple(rank,dims,NULL),prop=H5Pcreate(H5P_DATASET_CREATE),d=-1;
    if(space<0||prop<0)goto done;
    if(gzip&&dims[0]){
        hsize_t chunk[2]={dims[0]<65536?dims[0]:65536,rank==2?dims[1]:0};
        if(H5Pset_chunk(prop,rank,chunk)<0||H5Pset_shuffle(prop)<0||H5Pset_deflate(prop,1)<0)goto done;
    }
    d=H5Dcreate2(f,name,type,space,H5P_DEFAULT,prop,H5P_DEFAULT);
done:if(space>=0)H5Sclose(space);if(prop>=0)H5Pclose(prop);return d;
}
static int scalar_attr(hid_t f,const char *name,unsigned *value,int write){
    hid_t s=-1,a=-1;int rc=-1;
    if(write){s=H5Screate(H5S_SCALAR);if(s>=0)a=H5Acreate2(f,name,H5T_STD_U32LE,s,H5P_DEFAULT,H5P_DEFAULT);}
    else a=H5Aopen(f,name,H5P_DEFAULT);
    if(a<0)goto done;
    if(!write){s=H5Aget_space(a);if(s<0||H5Sget_simple_extent_type(s)!=H5S_SCALAR)goto done;}
    rc=write?H5Awrite(a,H5T_NATIVE_UINT,value):H5Aread(a,H5T_NATIVE_UINT,value);
done:if(a>=0)H5Aclose(a);if(s>=0)H5Sclose(s);return rc<0?-1:0;
}
static int shape(hid_t d,hid_t type,int rank,const hsize_t *dims){
    hid_t s=H5Dget_space(d),t=H5Dget_type(d);hsize_t actual[2]={0,0};int rc=-1;
    if(s>=0&&t>=0&&H5Tequal(t,type)>0&&H5Sget_simple_extent_ndims(s)==rank&&
       H5Sget_simple_extent_dims(s,actual,NULL)>=0){
        rc=0;for(int i=0;i<rank;i++)if(actual[i]!=dims[i])rc=-1;
    }
    if(s>=0)H5Sclose(s);
    if(t>=0)H5Tclose(t);
    return rc;
}
static int slice(hid_t d,hid_t type,int rank,const hsize_t *start,const hsize_t *n,void *p,int write){
    hid_t s=H5Dget_space(d),memory=H5Screate_simple(rank,n,NULL);int rc=-1;
    if(s>=0&&memory>=0&&H5Sselect_hyperslab(s,H5S_SELECT_SET,start,NULL,n,NULL)>=0)
        rc=write?H5Dwrite(d,type,memory,s,H5P_DEFAULT,p):H5Dread(d,type,memory,s,H5P_DEFAULT,p);
    if(s>=0)H5Sclose(s);
    if(memory>=0)H5Sclose(memory);
    return rc<0?-1:0;
}
typedef struct{hid_t file,d;unsigned id;uint64_t begin,end;unsigned char *cache;} source;
static int bytes(void *ctx,const optb_section *s,uint64_t pos,void *p,size_t n,char *e,size_t ne){
    source *c=ctx;unsigned char *out=p;
    if(c->id!=s->id){
        if(c->d>=0)H5Dclose(c->d);
        c->d=-1;c->id=s->id;c->begin=c->end=0;
        char name[64];snprintf(name,sizeof name,"data/%u",s->id);c->d=H5Dopen2(c->file,name,H5P_DEFAULT);
        hsize_t length=s->stored_bytes;
        if(c->d<0||shape(c->d,H5T_STD_U8LE,1,&length))return bad(e,ne,"invalid encoded HDF5 dataset");
    }
    if(pos>s->stored_bytes||n>s->stored_bytes-pos)return bad(e,ne,"HDF5 byte range overflow");
    while(n){
        if(pos<c->begin||pos>=c->end){
            hsize_t start=pos,count=s->stored_bytes-pos;if(count>CACHE_BYTES)count=CACHE_BYTES;
            if(!count||slice(c->d,H5T_NATIVE_UCHAR,1,&start,&count,c->cache,0))return bad(e,ne,"HDF5 byte read failed");
            c->begin=pos;c->end=pos+count;
        }
        size_t take=(size_t)(c->end-pos);if(take>n)take=n;
        memcpy(out,c->cache+(size_t)(pos-c->begin),take);pos+=take;out+=take;n-=take;
    }
    return 0;
}
static int copy(void *ctx,const optb_info *h,const optb_section *s,uint64_t start,uint64_t n,
                const void *p,char *e,size_t ne){
    (void)h;(void)e;(void)ne;optb_model *m=ctx;const optb_section *d=optb_get(m,s->id);
    memcpy((unsigned char*)d->data+start*optb_type_bytes(d->type),p,(size_t)n*optb_type_bytes(d->type));return 0;
}
static int read_manifest(hid_t f,optb_model *m,unsigned *layout,char *e,size_t ne){
    unsigned version=0;hid_t d=-1,space=-1;unsigned char *p=NULL;int rc=-1;
    if(scalar_attr(f,"optbin_hdf5_version",&version,0)||version!=1||scalar_attr(f,"layout",layout,0)||*layout>2)
        return bad(e,ne,"unsupported native HDF5 metadata/layout");
    d=H5Dopen2(f,"manifest",H5P_DEFAULT);if(d<0)goto done;
    space=H5Dget_space(d);hsize_t n=0;
    if(space<0||H5Sget_simple_extent_ndims(space)!=1||H5Sget_simple_extent_dims(space,&n,NULL)<0||
       n<128||n>128+4096*64||shape(d,H5T_STD_U8LE,1,&n))goto done;
    p=malloc((size_t)n);if(!p||H5Dread(d,H5T_NATIVE_UCHAR,H5S_ALL,H5S_ALL,H5P_DEFAULT,p)<0)goto done;
    rc=optb_manifest_decode(p,128,p+128,(size_t)n-128,bqp_u64(p+32),m,e,ne);
done:free(p);if(space>=0)H5Sclose(space);if(d>=0)H5Dclose(d);
    return rc?bad(e,ne,"invalid native HDF5 manifest"):0;
}
int optb_h5_load(const char *path,optb_model *m,uint64_t limit,char *e,size_t ne){
    if(!m)return bad(e,ne,"null output model");
    memset(m,0,sizeof *m);
    hid_t f=H5Fopen(path,H5F_ACC_RDONLY,H5P_DEFAULT);int rc=-1;unsigned layout=0;
    source src={f,-1,0,0,0,NULL};
    if(f<0)return bad(e,ne,"cannot open HDF5 input");
    if(read_manifest(f,m,&layout,e,ne)||optb_allocate(m,limit,e,ne))goto done;
    if(!layout){
        src.cache=malloc(CACHE_BYTES);if(!src.cache){bad(e,ne,"HDF5 cache allocation failed");goto done;}
        if(optb_walk_encoded(m,bytes,&src,copy,m,e,ne))goto done;
    }else for(uint64_t k=0;k<m->info.sections;k++){
        optb_section *s=m->section+k;char name[64];hsize_t n=s->count;
        snprintf(name,sizeof name,"data/%u",s->id);hid_t d=H5Dopen2(f,name,H5P_DEFAULT);
        if(d<0){bad(e,ne,"missing typed HDF5 dataset");goto done;}
        int fail=shape(d,disk_type(s->type),1,&n)||(n&&H5Dread(d,mem_type(s->type),H5S_ALL,H5S_ALL,H5P_DEFAULT,s->data)<0);
        H5Dclose(d);if(fail){bad(e,ne,"invalid typed HDF5 dataset");goto done;}
        snprintf(name,sizeof name,"checks/%u",s->id);d=H5Dopen2(f,name,H5P_DEFAULT);
        hsize_t dims[2]={s->blocks,3};uint64_t *checks=NULL;
        if(d<0){bad(e,ne,"missing HDF5 block checks");goto done;}
        if(s->blocks>SIZE_MAX/(3*sizeof *checks)||shape(d,H5T_STD_U64LE,2,dims))fail=1;
        if(!fail&&s->blocks){
            checks=malloc((size_t)s->blocks*3*sizeof *checks);
            if(!checks||H5Dread(d,H5T_NATIVE_UINT64,H5S_ALL,H5S_ALL,H5P_DEFAULT,checks)<0)fail=1;
        }
        H5Dclose(d);uint64_t seen=0;
        for(uint64_t b=0;!fail&&b<s->blocks;b++){
            uint64_t start=checks[3*b],count=checks[3*b+1],crc=checks[3*b+2];
            if(start!=seen||!count||count>m->info.block_elements||count>s->count-seen||crc>UINT32_MAX){fail=1;break;}
            const unsigned char *p=(unsigned char*)s->data+start*optb_type_bytes(s->type);
            if((uint32_t)crc32(0,p,(uInt)(count*optb_type_bytes(s->type)))!=crc){fail=1;break;}seen+=count;
        }
        free(checks);if(fail||seen!=s->count){bad(e,ne,"HDF5 decoded checksum/coverage mismatch");goto done;}
    }
    if(optb_validate(m,e,ne))goto done;
    rc=0;
done:if(src.d>=0)H5Dclose(src.d);free(src.cache);H5Fclose(f);if(rc)optb_free(m);return rc;
}
typedef struct{const optb_model *m;hid_t *data,*check;uint64_t *seen;} output;
static int write_block(void *ctx,const optb_info *h,const optb_section *s,uint64_t start,uint64_t n,
                       const void *p,char *e,size_t ne){
    (void)h;output *o=ctx;uint64_t k=(uint64_t)(optb_get(o->m,s->id)-o->m->section);
    hsize_t offset=start,count=n,cs[2]={o->seen[k]++,0},cn[2]={1,3};
    uint64_t check[3]={start,n,(uint32_t)crc32(0,p,(uInt)(n*optb_type_bytes(s->type)))};
    if(slice(o->data[k],mem_type(s->type),1,&offset,&count,(void*)p,1)||
       slice(o->check[k],H5T_NATIVE_UINT64,2,cs,cn,check,1))return bad(e,ne,"HDF5 array/check write failed");
    return 0;
}
int optb_h5_export(const char *input,const char *target,unsigned layout,char *e,size_t ne){
    FILE *in=NULL;hid_t f=-1;optb_model m={0};int rc=-1;char *temp=NULL;
    unsigned char *manifest=NULL,*buffer=NULL;hid_t *data=NULL,*checks=NULL;uint64_t *seen=NULL;
    if(e&&ne)e[0]=0;
    if(layout>2)return bad(e,ne,"invalid HDF5 export layout");
    in=fopen(input,"rb");if(!in)return bad(e,ne,"cannot open export source");
    if(optb_manifest_read(in,&m,e,ne))goto done;
    size_t mn=128+(size_t)m.info.sections*64;manifest=malloc(mn);buffer=malloc(CACHE_BYTES);
    temp=calloc(strlen(target)+24,1);
    if(!manifest||!buffer||!temp){bad(e,ne,"export allocation failed");goto done;}
    rewind(in);if(fread(manifest,1,mn,in)!=mn){bad(e,ne,"manifest read failed");goto done;}
    sprintf(temp,"%s.partial.XXXXXX",target);int fd=mkstemp(temp);
    if(fd<0){bad(e,ne,"cannot create export temporary");goto done;}close(fd);
    f=H5Fcreate(temp,H5F_ACC_TRUNC,H5P_DEFAULT,H5P_DEFAULT);if(f<0)goto done;
    unsigned version=1;if(scalar_attr(f,"optbin_hdf5_version",&version,1)||scalar_attr(f,"layout",&layout,1))goto done;
    hsize_t mn64=mn;hid_t d=dataset(f,"manifest",H5T_STD_U8LE,1,&mn64,0);
    if(d<0)goto done;
    int fail=H5Dwrite(d,H5T_NATIVE_UCHAR,H5S_ALL,H5S_ALL,H5P_DEFAULT,manifest)<0;H5Dclose(d);if(fail)goto done;
    hid_t g=H5Gcreate2(f,"data",H5P_DEFAULT,H5P_DEFAULT,H5P_DEFAULT);if(g<0)goto done;H5Gclose(g);
    if(!layout){
        if(optb_visit(input,NULL,NULL,NULL,e,ne))goto done;
        for(uint64_t k=0;k<m.info.sections;k++){
            const optb_section *s=m.section+k;char name[64];snprintf(name,sizeof name,"data/%u",s->id);
            hsize_t length=s->stored_bytes;d=dataset(f,name,H5T_STD_U8LE,1,&length,0);if(d<0)goto done;
            fail=fseeko(in,(off_t)s->offset,SEEK_SET)!=0;
            for(uint64_t pos=0;!fail&&pos<s->stored_bytes;){
                hsize_t start=pos,n=s->stored_bytes-pos;if(n>CACHE_BYTES)n=CACHE_BYTES;
                fail=fread(buffer,1,(size_t)n,in)!=n||slice(d,H5T_NATIVE_UCHAR,1,&start,&n,buffer,1);pos+=n;
            }
            H5Dclose(d);if(fail)goto done;
        }
    }else{
        g=H5Gcreate2(f,"checks",H5P_DEFAULT,H5P_DEFAULT,H5P_DEFAULT);if(g<0)goto done;H5Gclose(g);
        size_t ns=(size_t)m.info.sections;data=malloc(ns*sizeof *data);checks=malloc(ns*sizeof *checks);seen=calloc(ns,sizeof *seen);
        if(data)for(size_t k=0;k<ns;k++)data[k]=-1;
        if(checks)for(size_t k=0;k<ns;k++)checks[k]=-1;
        if(!data||!checks||!seen)goto done;
        for(size_t k=0;k<ns;k++){
            const optb_section *s=m.section+k;char name[64];hsize_t n=s->count,dims[2]={s->blocks,3};
            snprintf(name,sizeof name,"data/%u",s->id);data[k]=dataset(f,name,disk_type(s->type),1,&n,layout==2);
            snprintf(name,sizeof name,"checks/%u",s->id);checks[k]=dataset(f,name,H5T_STD_U64LE,2,dims,0);
            if(data[k]<0||checks[k]<0)goto done;
        }
        output o={&m,data,checks,seen};if(optb_visit(input,NULL,write_block,&o,e,ne))goto done;
    }
    if(data)for(uint64_t k=0;k<m.info.sections;k++){if(data[k]>=0){H5Dclose(data[k]);data[k]=-1;}if(checks[k]>=0){H5Dclose(checks[k]);checks[k]=-1;}}
    if(H5Fclose(f)<0){f=-1;goto done;}f=-1;
    if(link(temp,target)){bad(e,ne,"output exists or cannot publish export");goto done;}rc=0;
done:
    if(data&&checks)for(uint64_t k=0;k<m.info.sections;k++){if(data[k]>=0)H5Dclose(data[k]);if(checks[k]>=0)H5Dclose(checks[k]);}
    if(f>=0)H5Fclose(f);
    if(in)fclose(in);
    if(temp){if(temp[0])unlink(temp);free(temp);}
    free(manifest);free(buffer);free(data);free(checks);free(seen);optb_free(&m);
    if(rc&&e&&ne&&!e[0])bad(e,ne,"native HDF5 export failed");
    return rc;
}
