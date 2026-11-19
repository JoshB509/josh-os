"""Keep immutable email images beside their copy during ordinary text edits."""
from difflib import SequenceMatcher


def rebase_images(old_body,new_body,images):
    # Match content lines rather than blank separators, so paragraph reflow and
    # removal of temporary image markers do not strand numeric line anchors.
    old=[(n,line.strip()) for n,line in enumerate(old_body.split('\n'),1) if line.strip()]
    new=[(n,line.strip()) for n,line in enumerate(new_body.split('\n'),1) if line.strip()]
    ops=SequenceMatcher(None,[x[1] for x in old],[x[1] for x in new],autojunk=False).get_opcodes()
    result=[]
    for image in images:
        count=sum(n<=image['after_line'] for n,_ in old)
        mapped=0
        if count:
            for tag,i1,i2,j1,j2 in ops:
                if i1<count<=i2:
                    if tag=='equal':mapped=j1+count-i1
                    elif tag=='delete':mapped=j1
                    elif count==i2:mapped=j2
                    else:
                        raise ValueError('This rewrite removes the text around an image. Save explicit image placements with the body so its position can be reviewed.')
                    break
        result.append(dict(image,after_line=new[mapped-1][0] if mapped else 0))
    return result
