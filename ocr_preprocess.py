"""Local background normalization and bounded circular text unwrapping."""
import math
import io
from PIL import Image, ImageOps, ImageFilter, ImageChops, ImageMath


def _solve(matrix, vector):
    rows=[list(row)+[value] for row,value in zip(matrix,vector)]
    for column in range(3):
        pivot=max(range(column,3),key=lambda i:abs(rows[i][column]))
        if abs(rows[pivot][column])<1e-8:
            return None
        rows[column],rows[pivot]=rows[pivot],rows[column]
        value=rows[column][column]
        rows[column]=[v/value for v in rows[column]]
        for index in range(3):
            if index!=column:
                value=rows[index][column]
                rows[index]=[a-value*b for a,b in zip(rows[index],rows[column])]
    return [row[3] for row in rows]


def ink_mask(gray):
    width,height=gray.size
    smooth=gray.filter(ImageFilter.MedianFilter(3))
    background=smooth.filter(ImageFilter.MaxFilter(25)).filter(ImageFilter.GaussianBlur(4))
    normalized=ImageMath.lambda_eval(lambda args:args['image']*255/(args['background']+1),image=smooth,background=background).convert('L')
    binary=normalized.point(lambda value:0 if value<155 else 255)
    line=binary
    for offset in (1,2,4,8,16):line=ImageChops.lighter(line,ImageChops.offset(line,offset,0))
    for offset in (1,2,4,8,16):line=ImageChops.darker(line,ImageChops.offset(line,-offset,0))
    binary=ImageChops.lighter(binary,ImageOps.invert(line))
    raw=list(binary.get_flattened_data())
    mask=bytearray(1 if value<128 else 0 for value in raw)
    visited=bytearray(width*height);ink=[]
    for index,marked in enumerate(mask):
        if not marked or visited[index]: continue
        stack=[index];visited[index]=1;component=[];edge=False
        while stack:
            item=stack.pop();component.append(item);x=item%width;y=item//width
            if x<2 or y<2 or x>=width-2 or y>=height-2:edge=True
            for dy in (-1,0,1):
                for dx in (-1,0,1):
                    xx=x+dx;yy=y+dy
                    if 0<=xx<width and 0<=yy<height:
                        other=yy*width+xx
                        if mask[other] and not visited[other]:
                            visited[other]=1;stack.append(other)
        component_width=max(value%width for value in component)-min(value%width for value in component)+1
        component_height=max(value//width for value in component)-min(value//width for value in component)+1
        if not edge and len(component)>=15 and component_width>=3 and component_height>=6:
            ink.extend((value%width,value//width) for value in component)
    image=Image.new('L',gray.size,255)
    pixels=image.load()
    for x,y in ink: pixels[x,y]=0
    return image,ink


def unwrap_arc(gray, ink=None):
    width,height=gray.size
    if width<80 or height<35: return None
    if ink is None: _,ink=ink_mask(gray)
    buckets={}
    for x,y in ink: buckets.setdefault(x//6,[]).append((x,y))
    centres=[(sum(x for x,y in values)/len(values),sum(y for x,y in values)/len(values))
             for values in buckets.values() if len(values)>=8]
    if len(centres)<10 or len(ink)<100: return None
    features=[(x,y,1) for x,y in centres]
    matrix=[[sum(row[i]*row[j] for row in features) for j in range(3)] for i in range(3)]
    vector=[-sum(row[i]*(x*x+y*y) for row,(x,y) in zip(features,centres)) for i in range(3)]
    solution=_solve(matrix,vector)
    if solution is None: return None
    a,b,c=solution;cx,cy=-a/2,-b/2
    return polar_unwrap(gray,ink,cx,cy)


def polar_unwrap(gray,ink,cx,cy):
    width,height=gray.size
    direction=1 if sum(y for x,y in ink)/len(ink)>cy else -1
    polar=sorted((math.hypot(x-cx,y-cy),math.atan2(x-cx,direction*(y-cy))) for x,y in ink)
    radius_min=polar[len(polar)//50][0]-7;radius_max=polar[-len(polar)//50-1][0]+7
    angles=sorted(angle for radius,angle in polar)
    padding=max(.12,math.atan2(height*.25,(radius_min+radius_max)/2))
    angle_min=max(-1.5,angles[len(angles)//100]-padding);angle_max=min(1.5,angles[-len(angles)//100-1]+padding)
    if not 10<radius_max-radius_min<height or not .4<angle_max-angle_min<3.05 or radius_min<=0:return None
    output_width=min(1000,max(100,int((angle_max-angle_min)*(radius_min+radius_max)/2)))
    output_height=min(250,max(25,int(radius_max-radius_min)))
    def point(x,y):
        angle=angle_min+(angle_max-angle_min)*x/output_width
        fraction=y/output_height if direction==1 else 1-y/output_height
        radius=radius_min+(radius_max-radius_min)*fraction
        return cx+radius*math.sin(angle),cy+direction*radius*math.cos(angle)
    mesh=[]
    for x in range(0,output_width,4):
        for y in range(0,output_height,4):
            right=min(x+4,output_width);bottom=min(y+4,output_height)
            mesh.append(((x,y,right,bottom),(*point(x,y),*point(x,bottom),*point(right,bottom),*point(right,y))))
    return gray.transform((output_width,output_height),Image.Transform.MESH,mesh,Image.Resampling.BICUBIC,fillcolor=255)


def unwrap_curve(gray,ink):
    import bisect
    width,height=gray.size
    buckets={}
    for x,y in ink:buckets.setdefault(x//6,[]).append((x,y))
    points=[(sum(x for x,y in values)/len(values),sum(y for x,y in values)/len(values)) for values in buckets.values() if len(values)>=8]
    if len(points)<10:return None
    features=[(x*x,x,1) for x,y in points]
    matrix=[[sum(row[i]*row[j] for row in features) for j in range(3)] for i in range(3)]
    vector=[sum(row[i]*y for row,(x,y) in zip(features,points)) for i in range(3)]
    solution=_solve(matrix,vector)
    if solution is None:return None
    a,b,c=solution
    if not .0003<abs(a)<.1:return None
    first=max(0,min(x for x,y in ink)-8);last=min(width-1,max(x for x,y in ink)+8)
    xs=list(range(first,last+1));length=[0.]
    for x in xs[1:]:length.append(length[-1]+math.sqrt(1+(2*a*x+b)**2))
    offsets=sorted((y-(a*x*x+b*x+c))/math.sqrt(1+(2*a*x+b)**2) for x,y in ink)
    low=offsets[0]-5;high=offsets[-1]+5
    if not 10<high-low<height:return None
    ow=min(1200,max(100,int(length[-1])));oh=min(250,max(25,int(high-low)))
    def point(u,v):
        distance=u/ow*length[-1];index=min(len(xs)-1,bisect.bisect_left(length,distance))
        x=xs[index]
        if index>0:x=xs[index-1]+(distance-length[index-1])/(length[index]-length[index-1])
        slope=2*a*x+b;normal=math.sqrt(1+slope*slope);offset=low+v/oh*(high-low)
        return x-slope*offset/normal,a*x*x+b*x+c+offset/normal
    mesh=[]
    for x in range(0,ow,4):
        for y in range(0,oh,4):
            right=min(x+4,ow);bottom=min(y+4,oh)
            mesh.append(((x,y,right,bottom),(*point(x,y),*point(x,bottom),*point(right,bottom),*point(right,y))))
    return gray.transform((ow,oh),Image.Transform.MESH,mesh,Image.Resampling.BICUBIC,fillcolor=255)


def variants(image):
    with Image.open(io.BytesIO(image)) as source:
        if source.width*source.height>2_000_000 or max(source.size)>4096:
            return []
        if source.mode in ('RGBA','LA'):
            base=Image.new('RGBA',source.size,'white');base.alpha_composite(source.convert('RGBA'));gray=ImageOps.grayscale(base)
        else:
            gray=ImageOps.grayscale(source)
        gray.thumbnail((900,400))
    mask,ink=ink_mask(gray)
    result=[('gray',gray),('mask',mask)]
    arc=unwrap_arc(gray,ink);arc_mask=unwrap_arc(mask,ink)
    if arc is not None: result.extend([('arc',arc),('arc-mask',arc_mask)])
    curve=unwrap_curve(gray,ink);curve_mask=unwrap_curve(mask,ink)
    if curve is not None:result.extend([('curve',curve),('curve-mask',curve_mask)])
    if len(ink)>100:
        for xfactor in (.45,.5,.55):
            for yfactor in (-.4,-.15,.1,.9,1.15,1.4):
                guess=polar_unwrap(gray,ink,gray.width*xfactor,gray.height*yfactor)
                if guess is not None:result.append(('polar-'+str(xfactor)+'-'+str(yfactor),guess))
    prepared=[]
    for family,value in result:
        value=ImageOps.autocontrast(value)
        value=value.resize((value.width*2,value.height*2),Image.Resampling.LANCZOS)
        padded=ImageOps.expand(value,border=16,fill='white')
        prepared.append((family,padded))
        if family in ('arc','curve'):
            for threshold in (85,105,125):
                prepared.append((family+'-bin'+str(threshold),padded.point(lambda p:0 if p<threshold else 255)))
    return prepared
